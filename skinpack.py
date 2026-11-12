"""Skin packs: a small zip file for sharing a skin or a recolor with other people.

A pack contains ONLY what the author made: their edited PNGs and/or palette color changes,
plus a manifest. It never contains the game's own files, so it is small and safe to share.
The importer re-snaps every PNG against the importing player's own game files.

Format (version 1) is documented in docs/skin-pack-format.md.

Packs from other people are untrusted: sizes and counts are bounded, nothing is extracted
by path (frame names are looked up in the game's own frame list), and text is only ever
shown as text.
"""
import base64
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

import core
import mods
from core import EditorError

FORMAT = 1
KIND = "avatar-legends-skin"
MANIFEST = "skin.json"
MAX_PACK = 256 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_MEMBER = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
TOOL = "avatar-legends-skin-editor"


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _slug(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:40] or "skin"


# ----------------------------------------------------------------- export

def _preview_bytes(project, ws, edits, palettes):
    """A small PNG showing the skin (best effort; None if anything goes wrong)."""
    from PIL import Image
    try:
        stem = next((s for s in sorted(edits) if ws.frame_info(s)["type"] == 3), None)
        slot = next(iter(sorted(palettes)), None)
        if stem is None:                       # recolor only: show an idle frame with the colors
            stem = next((s for category in ("idle", "poses") for names in
                         (ws.tree.get(category, {}).get(m, {}).get("frames", [])
                          for m in sorted(ws.tree.get(category, {})))
                         for s in names if ws.frame_info(s)["type"] == 3 and not ws.frames[s]["mask"]), None)
        if stem is None:
            return None
        data = project.render(ws, stem, slot or ws.default_palette["name"])
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            im.thumbnail((512, 512), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    except Exception:
        return None


def create(project, ws, name, author="", description="", frames=True, palettes=True):
    """Write exports/<name>-<character>.zip from the project's edits. Returns the report."""
    edits = {s: e for s, e in project.edits().items() if s in ws.frames} if frames else {}
    colors = ({n: c for n, c in project.palette_edits().items() if n in ws.costume}
              if palettes else {})
    if not edits and not colors:
        raise EditorError("nothing to share: no edited frames or palette colors selected")
    name = _text(name, 80) or "My skin"

    manifest = {
        "format": FORMAT, "kind": KIND, "tool": TOOL,
        "name": name, "author": _text(author, 80), "description": _text(description, 2000),
        "character": project.char, "created": time.strftime("%Y-%m-%d"),
        "pak_sha256": ws.digest,
        "frames": [], "palettes": {},
    }
    mods.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{_slug(name)}-{project.char}"
    path = mods.EXPORT_DIR / f"{base}.zip"
    n = 2
    while path.exists():                       # never overwrite an earlier export
        path = mods.EXPORT_DIR / f"{base}-{n}.zip"
        n += 1
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for stem in sorted(edits, key=core.natural_key):
            png = project.edit_png(stem)
            if not png.is_file():
                raise EditorError(f"edited PNG is missing: {png.name}")
            blob = png.read_bytes()
            entry = {"name": stem, "sha256": hashlib.sha256(blob).hexdigest()}
            info = ws.frame_info(stem)
            box = edits[stem].get("box")
            if box and list(box) != [info["x"], info["y"], info["w"], info["h"]]:
                entry["box"] = list(box)       # where this bigger PNG sits on the screen canvas
            manifest["frames"].append(entry)
            zf.writestr(f"frames/{stem}.png", blob)
        for slot, cells in sorted(colors.items()):
            rows = manifest["palettes"].setdefault(slot, {})
            for (row, col), rgb in sorted(cells.items()):
                rows.setdefault(str(row), {})[str(col)] = list(rgb)
        preview = _preview_bytes(project, ws, edits, colors)
        if preview:
            zf.writestr("preview.png", preview)
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
    tmp.replace(path)
    return {"path": str(path), "frames": len(edits), "colors": sum(len(c) for c in colors.values()),
            "size": path.stat().st_size, "preview": bool(preview)}


# ----------------------------------------------------------------- reading (untrusted)

def _read_member(zf, member, limit=MAX_MEMBER):
    try:
        info = zf.getinfo(member)
    except KeyError:
        return None
    if info.file_size > limit:
        raise EditorError(f"{member} in the pack is too large")
    data = zf.read(info)
    if len(data) > limit:
        raise EditorError(f"{member} in the pack is too large")
    return data


def open_pack(data):
    """Parse and validate a pack. Returns (manifest, ZipFile). Raises EditorError."""
    if len(data) > MAX_PACK:
        raise EditorError("this file is too large to be a skin pack")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise EditorError("this is not a skin pack (not a zip file)") from err
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES + 4:
        raise EditorError("this pack has too many files")
    if sum(i.file_size for i in infos) > MAX_TOTAL:
        raise EditorError("this pack is unreasonably large when unpacked")
    raw = _read_member(zf, MANIFEST, MAX_MANIFEST)
    if raw is None:
        raise EditorError(f"this zip has no {MANIFEST}, so it is not a skin pack")
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise EditorError("the pack's skin.json is not valid") from err
    if not isinstance(m, dict) or m.get("kind") != KIND:
        raise EditorError("this zip is not an Avatar Legends skin pack")
    if m.get("format") != FORMAT:
        raise EditorError(f"this pack uses format {m.get('format')!r}; this editor reads format "
                          f"{FORMAT}. Update the editor.")
    try:
        character = core.check_char(m.get("character"))
    except EditorError as err:
        raise EditorError("the pack names an invalid character") from err

    frames, boxes = [], {}
    for entry in (m.get("frames") or [])[:MAX_ENTRIES]:
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str) and 0 < len(name) <= 300 and "/" not in name and "\\" not in name:
            frames.append(name)
            box = entry.get("box")
            if (isinstance(box, list) and len(box) == 4
                    and all(isinstance(v, int) and 0 <= v <= 16384 for v in box)):
                boxes[name] = box
    colors = {}
    palettes = m.get("palettes") if isinstance(m.get("palettes"), dict) else {}
    for slot, rows in list(palettes.items())[:100]:
        cells = {}
        if isinstance(slot, str) and isinstance(rows, dict):
            for row, cols in rows.items():
                if not isinstance(cols, dict):
                    continue
                for col, rgb in cols.items():
                    try:
                        key = (int(row), int(col))
                        color = tuple(int(v) for v in rgb)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= key[0] < 64 and 0 <= key[1] < 3 and len(color) == 3 \
                            and all(0 <= v <= 255 for v in color):
                        cells[key] = color
        if cells:
            colors[slot[:80]] = cells
    manifest = {
        "name": _text(m.get("name"), 80) or "Untitled skin",
        "author": _text(m.get("author"), 80),
        "description": _text(m.get("description"), 2000),
        "character": character,
        "created": _text(m.get("created"), 20),
        "pak_sha256": _text(m.get("pak_sha256"), 64).upper(),
        "frames": frames, "boxes": boxes, "palettes": colors,
    }
    return manifest, zf


def _png_size(blob):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(blob)) as im:
            return im.size if im.format == "PNG" else None
    except Exception:
        return None


def analyze(data, ws, project):
    """What importing this pack would do, without changing anything."""
    manifest, zf = open_pack(data)
    mismatch = manifest["character"] != project.char
    report = {k: manifest[k] for k in ("name", "author", "description", "character", "created")}
    report.update(
        character_ok=not mismatch,
        pak_match=(manifest["pak_sha256"] == ws.digest) if manifest["pak_sha256"] else None,
        slots=list(ws.costume), frames=[], palettes=[], preview=None)
    preview = _read_member(zf, "preview.png", 4 * 1024 * 1024)
    if preview and _png_size(preview):
        report["preview"] = "data:image/png;base64," + base64.b64encode(preview).decode("ascii")
    if mismatch:
        return report
    have = project.edits()
    for stem in manifest["frames"]:
        item = {"name": stem, "label": " / ".join(stem.split("~")[2:]) or stem}
        blob = _read_member(zf, f"frames/{stem}.png") if stem in ws.frames else None
        if stem not in ws.frames:
            item.update(state="missing", detail="not in your version of the game")
        elif blob is None:
            item.update(state="bad", detail="PNG missing from the pack")
        else:
            size, info = _png_size(blob), ws.frame_info(stem)
            if size is None:
                item.update(state="bad", detail="not a valid PNG")
            else:
                try:
                    if info["type"] == 6 and size != (info["w"], info["h"]):
                        raise EditorError(f"{size[0]}×{size[1]}, game needs {info['w']}×{info['h']}")
                    if info["type"] == 3:
                        project._box_for(ws, stem, size, manifest["boxes"].get(stem))
                    item.update(state="overwrite" if stem in have else "new", detail="")
                except EditorError as err:
                    item.update(state="size", detail=str(err)[:160])
        report["frames"].append(item)
    mine = project.palette_edits()
    for slot, cells in sorted(manifest["palettes"].items()):
        report["palettes"].append({"slot": slot, "colors": len(cells), "target_ok": slot in ws.costume,
                                   "conflict": bool(mine.get(slot))})
    return report


def apply(data, ws, project, overwrite=False, slot_map=None):
    """Import the pack into the project. Frames are re-snapped to this game's palette."""
    manifest, zf = open_pack(data)
    if manifest["character"] != project.char:
        raise EditorError(f"this skin is for {manifest['character']}, not {project.char}")
    slot_map = slot_map if isinstance(slot_map, dict) else {}
    have = project.edits()
    done = {"frames": [], "palettes": []}
    for stem in manifest["frames"]:
        row = {"name": stem, "label": " / ".join(stem.split("~")[2:]) or stem}
        try:
            if stem not in ws.frames:
                raise EditorError("not in your version of the game")
            if stem in have and not overwrite:
                row["state"] = "kept"
                row["detail"] = "you already edited this frame"
            else:
                blob = _read_member(zf, f"frames/{stem}.png")
                if blob is None:
                    raise EditorError("PNG missing from the pack")
                result = project.replace(ws, stem + ".png", blob, box=manifest["boxes"].get(stem))
                row.update(state="imported", mean_error=result["mean_error"],
                           far_pixels=result["far_pixels"])
        except EditorError as err:
            row.update(state="failed", detail=str(err))
        done["frames"].append(row)
    mine = project.palette_edits()
    for slot, cells in sorted(manifest["palettes"].items()):
        target = slot_map.get(slot, slot)
        row = {"slot": slot, "target": target, "colors": len(cells)}
        if target == "":
            row.update(state="skipped", detail="skipped by you")
        elif target not in ws.costume:
            row.update(state="failed", detail=f"you have no palette named {target}")
        elif mine.get(target) and not overwrite:
            row.update(state="kept", detail=f"{target} already has your colors")
        else:
            project.set_colors(ws, target, cells)
            row["state"] = "imported"
        done["palettes"].append(row)
    return done
