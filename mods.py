"""Mod projects: export, drag & drop replace (snap), build, install and restore.

A project folder (skin-editor/projects/<name>/) holds ONLY the user's edited PNGs,
their exported work files and manifest.json. Everything built from them lives in
skin-editor/build/<name>/ and can be deleted at any time.

The only functions that write into the game folder are install() and restore().
"""
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import threading
import time
from collections import Counter
from pathlib import Path

import core
from core import EditorError

import snap_frame as sf                      # core put avatar-legends-tools on sys.path
import type6
from munged_encode import lz4_literal
from munged_extract import apply_palette, decompress_lz4, read_munged, unpack_tiles
from pak_extract import PakArchive, PakError

PROJECTS_DIR = core.DATA_DIR / "projects"
EXPORT_DIR = core.DATA_DIR / "exports"
INSTALLED_PATH = core.DATA_DIR / "installed.json"
MAX_PNG_BYTES = 64 * 1024 * 1024
PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_project_lock = threading.RLock()
_build_locks = {}          # (character, frame) -> lock, so one frame is never rebuilt twice at once


# ----------------------------------------------------------------- snapping (type 3)

def _known_rows(ws):
    """Rows from rows_known.json (made by make_rows_known.py), if it has this exact pak."""
    try:
        item = json.loads((core.APP_DIR / "rows_known.json").read_text(encoding="ascii")).get(ws.char)
        if item["sha256"] == ws.digest:
            return [int(r) for r in item["rows"]]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def warm(ws):
    """Work out allowed_rows in the background (once), so the first export or drop for a
    character does not wait for it. Only does real work for a pak rows_known.json does not
    cover, such as after a game update."""
    if getattr(ws, "_warming", False) or ws._cands is not None:
        return
    ws._warming = True

    def work():
        try:
            allowed_rows(ws)
        except EditorError:
            pass
    threading.Thread(target=work, daemon=True).start()


def allowed_rows(ws):
    """Palette rows the character's own frames really use (cached per pak).

    This is what snap_frame.py --ref-dir <frames folder> computes. Slow the first time
    (it reads every character frame), then stored in the cache folder.
    """
    with ws._cands_lock:
        if ws._cands is not None:
            return ws._cands
        if ws.default_palette is None:
            raise EditorError("no costume palette found for this character")
        rows_file = ws.root / "rows.json"
        rows = None
        if rows_file.is_file():
            try:
                rows = [int(r) for r in json.loads(rows_file.read_text(encoding="ascii"))]
            except (OSError, ValueError, TypeError):
                rows = None
        if rows is None:
            rows = _known_rows(ws)                 # worked out ahead of time for this exact pak
        if rows is None:
            stems = [stem for stem, e in ws.frames.items()
                     if e["kind"] == "frames" and not e["mask"]]
            if not stems:                      # paks without a "frames" group
                stems = [stem for stem, e in ws.frames.items() if not e["mask"]]
            try:
                rows = sf.rows_used([ws.frames_dir / f"{s}.munged" for s in stems], 30)
            except (ValueError, OSError) as err:
                raise EditorError(f"could not read the character's frames: {err}") from err
            rows_file.write_text(json.dumps(rows), encoding="ascii")
        rows = [r for r in rows if 0 < r < 64]
        if not rows:
            raise EditorError("no usable palette rows found")
        ws._cands = rows
        return rows


OWN_ROW_BONUS = 12.0       # how much closer another row's color must be to win
FAR_DISTANCE = 40          # same "far from the palette" threshold as snap_frame.py
SNAP_VERSION = "s3"        # bump when snapping changes, so existing edits are rebuilt
SAME_TOLERANCE = 2         # a pixel this close to the export counts as "not repainted"
ALPHA_CUT = 6              # below this a painted pixel is empty

# Line strength (material G): 255 = no line, 0 = full black line.
EDGE_LEVELS = (255, 240, 224, 208, 192, 176, 160, 144, 128, 112, 96, 80, 64, 48, 32, 16, 0)
SOFT_FILL = (24, 48, 72, 96, 120)       # material B below 128 = partly transparent fill
FRINGE_BLUE = 242                        # what the shipped frames use on the outline's soft edge

# The 4th channel of a character frame is a soft "depth inside the silhouette" map:
# mean value by distance from the silhouette edge in pixels (measured on aang, katara and
# zuko frames; identical within 2 for all three). Outside: one ring of faint values.
DEPTH_ALPHA = (0, 78, 103, 129, 152, 170, 187, 201, 213, 223, 230, 236, 242, 246, 249, 251, 253)
RING_ALPHA = {3: 59, 4: 38}              # chamfer distance (3 = straight, 4 = diagonal) -> value
DIRTY_RADIUS = 18                        # px around a silhouette change where depth is redrawn


def _candidates(palette, rows, fringe):
    """[(premultiplied rgb, alpha, material RGB bytes)] — what each choice looks like in game."""
    out, seen = [], set()

    def add(red, green, blue):
        px = core.render_material(bytes((red, green, blue, 255)), palette)
        if px[3] == 0 or bytes(px) in seen:
            return
        seen.add(bytes(px))
        k = px[3] / 255.0
        out.append(((px[0] * k, px[1] * k, px[2] * k), px[3], bytes((red, green, blue))))

    for row in rows:
        for blue in sf.SHADES:
            for green in EDGE_LEVELS:
                add(row, green, blue)
        for blue in SOFT_FILL:
            add(row, 255, blue)
    if fringe:                                   # the outline alone, any strength (row 0 = no fill)
        for green in range(0, 255, 6):
            add(0, green, FRINGE_BLUE)
    return out


def snap_candidates(ws, stem, palette=None):
    """(own, others): the colors a palette (default: the default costume's) can show for one frame.

    'own' are the palette rows the ORIGINAL frame already uses (plus the bare outline),
    'others' every other row the character uses. Keeping pixels on the frame's own rows
    matters: a support character and the fighter often share near-identical colors on
    different rows, and a pixel that lands on the wrong row is recolored wrongly by every
    other costume.
    """
    rows = allowed_rows(ws)
    try:
        own = [r for r in sf.rows_used([ws.frame_path(stem)], 1) if 0 < r < 64]
    except (ValueError, OSError):
        own = []
    palette = palette or ws.default_palette["palette"]
    return (_candidates(palette, own, True),
            _candidates(palette, [r for r in rows if r not in own], False))


def _chamfer(seeds, w, h):
    """Distance (3 per straight step, 4 per diagonal) from every pixel to the nearest seed."""
    far = 1 << 30
    d = [0 if s else far for s in seeds]
    for y in range(h):
        base = y * w
        for x in range(w):
            i = base + x
            v = d[i]
            if v:
                if x and d[i - 1] + 3 < v:
                    v = d[i - 1] + 3
                if y:
                    if d[i - w] + 3 < v:
                        v = d[i - w] + 3
                    if x and d[i - w - 1] + 4 < v:
                        v = d[i - w - 1] + 4
                    if x < w - 1 and d[i - w + 1] + 4 < v:
                        v = d[i - w + 1] + 4
                d[i] = v
    for y in range(h - 1, -1, -1):
        base = y * w
        for x in range(w - 1, -1, -1):
            i = base + x
            v = d[i]
            if v:
                if x < w - 1 and d[i + 1] + 3 < v:
                    v = d[i + 1] + 3
                if y < h - 1:
                    if d[i + w] + 3 < v:
                        v = d[i + w] + 3
                    if x < w - 1 and d[i + w + 1] + 4 < v:
                        v = d[i + w + 1] + 4
                    if x and d[i + w - 1] + 4 < v:
                        v = d[i + w - 1] + 4
                d[i] = v
    return d


def _redraw_depth(material, original, w, h):
    """Fix the 4th channel after the silhouette changed (new hair, a wig, a cut-out).

    Pixels far from any change keep the original value. Near a change the value is redrawn
    from the distance to the new silhouette edge, following DEPTH_ALPHA.
    """
    n = w * h
    new_in = [not (material[i * 4] == 0 and material[i * 4 + 1] == 255) for i in range(n)]
    old_in = [not (original[i * 4] == 0 and original[i * 4 + 1] == 255) for i in range(n)]
    changed = [a != b for a, b in zip(new_in, old_in)]
    if not any(changed):
        return 0
    near = _chamfer(changed, w, h)
    depth = _chamfer([not v for v in new_in], w, h)           # inside: distance to outside
    outside = _chamfer(new_in, w, h)                           # outside: distance to inside
    limit, top, redrawn = DIRTY_RADIUS * 3, len(DEPTH_ALPHA) - 1, 0
    for i in range(n):
        if near[i] > limit:
            continue
        if new_in[i]:
            px = depth[i] / 3.0
            lo = min(top, int(px))
            hi = min(top, lo + 1)
            value = round(DEPTH_ALPHA[lo] + (DEPTH_ALPHA[hi] - DEPTH_ALPHA[lo]) * (px - int(px))) if lo < top else DEPTH_ALPHA[top]
            material[i * 4 + 3] = max(1, value)
        else:
            material[i * 4:i * 4 + 4] = bytes((0, 255, 0, RING_ALPHA.get(outside[i], 0)))
        redrawn += 1
    return redrawn


def snap(rgba, own, others, original, shown_now, shown_before, w, h):
    """Painted RGBA -> (material bytes, mean error, Counter of far-off colors, stats).

    original      the game's own material for this frame
    shown_now     what the editor exports for it today (render_material)
    shown_before  what older versions exported (hard outline edge)

    1. A pixel that still equals the export (either version) was not repainted: the game's
       own pixel is kept exactly, including the soft outline edge and the 4th channel.
    2. A repainted pixel is matched to the nearest look the palette can produce, comparing
       color and transparency together. It only leaves the frame's own palette rows when
       another row is clearly closer (by more than OWN_ROW_BONUS).
    """
    def nearest(cands, pr, pg, pb, a):
        best, best_d = None, 1e18
        for cand in cands:
            c = cand[0]
            d = (c[0] - pr) ** 2 + (c[1] - pg) ** 2 + (c[2] - pb) ** 2 + 2.0 * (cand[1] - a) ** 2
            if d < best_d:
                best, best_d = cand, d
        return best, math.sqrt(best_d) if best is not None else 1e9

    def same(i, other):
        if rgba[i + 3] < ALPHA_CUT and other[i + 3] < ALPHA_CUT:
            return True
        return (abs(rgba[i] - other[i]) <= SAME_TOLERANCE and abs(rgba[i + 1] - other[i + 1]) <= SAME_TOLERANCE
                and abs(rgba[i + 2] - other[i + 2]) <= SAME_TOLERANCE and abs(rgba[i + 3] - other[i + 3]) <= SAME_TOLERANCE)

    material = bytearray(original)
    cache, total, counted, poor, kept = {}, 0.0, 0, Counter(), 0
    for i in range(0, len(rgba), 4):
        if same(i, shown_now) or same(i, shown_before):
            kept += 1
            if rgba[i + 3] >= ALPHA_CUT:
                counted += 1
            continue
        r, g, b, a = rgba[i:i + 4]
        if a < ALPHA_CUT:
            material[i:i + 4] = sf.TRANSPARENT
            continue
        hit = cache.get((r, g, b, a))
        if hit is None:
            k = a / 255.0
            pr, pg, pb = r * k, g * k, b * k
            mine, mine_d = nearest(own, pr, pg, pb, a)
            if mine_d > OWN_ROW_BONUS:                    # not already a clear own-row match
                other, other_d = nearest(others, pr, pg, pb, a)
                if other_d + OWN_ROW_BONUS < mine_d:
                    mine, mine_d = other, other_d
            hit = cache[(r, g, b, a)] = (mine[2], mine_d)
        material[i:i + 3] = hit[0]
        if material[i + 3] == 0:                           # was empty: give it a value for now
            material[i + 3] = DEPTH_ALPHA[1]
        total += hit[1]
        counted += 1
        if hit[1] > FAR_DISTANCE:
            poor[(r, g, b)] += 1
    redrawn = _redraw_depth(material, original, w, h)
    pixels = len(rgba) // 4
    stats = {"kept_pixels": kept, "repainted_pixels": pixels - kept, "outline_redrawn": redrawn}
    return bytes(material), (total / counted if counted else 0.0), poor, stats


def encode_type3_at(material, w, h, orig_path, x, y):
    """Character frame bytes for material of size w x h placed at (x, y) on the canvas.

    Keeps the original's header (flags, asset key, source name, canvas). Pixel data is split
    into 591872-byte chunks like every shipped frame.
    """
    orig_path = Path(orig_path)
    info = read_munged(orig_path)[0]
    blob = bytearray(sf.synth_type3(material, w, h, source_name=info["source"],
                                    template_prefix=orig_path.read_bytes()))
    name_len = struct.unpack_from("<I", blob, 27)[0]
    off = 27 + 4 + name_len
    struct.pack_into("<6I", blob, off, x, y, w, h, info["canvas_width"], info["canvas_height"])
    tile_count = struct.unpack_from("<I", blob, off + 14 * 4)[0]
    pos = off + 60 + (tile_count + 7) // 8
    if struct.unpack_from("<I", blob, pos)[0] != 1:
        raise ValueError("unexpected chunk layout from synth_type3")
    _, raw, size = struct.unpack_from("<3I", blob, pos + 4)
    atlas = decompress_lz4(bytes(blob[pos + 16:pos + 16 + size]), raw)
    out = bytearray(blob[:pos])
    pieces = [atlas[i:i + type6.CHUNK_BYTES] for i in range(0, len(atlas), type6.CHUNK_BYTES)] or [b""]
    out += struct.pack("<I", len(pieces))
    for piece in pieces:
        stream = lz4_literal(piece)
        out += struct.pack("<3I", len(stream) + 8, len(piece), len(stream)) + stream
    return bytes(out)


# ----------------------------------------------------------------- projects

def _migrate_old_layout(root):
    """Early versions kept one character directly in projects/<name>/. Move it into
    projects/<name>/<character>/ (nothing is deleted)."""
    old = root / "manifest.json"
    with _project_lock:
        if not old.is_file():
            return
        try:
            char = core.check_char(json.loads(old.read_text(encoding="utf-8")).get("character"))
        except (OSError, ValueError, EditorError):
            return
        target = root / char
        if target.exists():
            return
        target.mkdir(parents=True)
        for name in ("edits", "work", "manifest.json"):
            if (root / name).exists():
                (root / name).rename(target / name)


def project_summary(name, char):
    """(edited frames, changed palette colors) without creating anything on disk."""
    try:
        project = Project(name, char)
        return len(project.edits()), project.palette_color_count()
    except EditorError:
        return 0, 0


SMALL_BOX = 256                 # thumbnails are recoloured at this size, then smoothed down to 192
_small_materials = {}           # (cache folder, frame, edit hash) -> (type, w, h, shrunken material)
_small_lock = threading.Lock()


class Project:
    def __init__(self, name, char):
        if not isinstance(name, str) or not PROJECT_RE.fullmatch(name):
            raise EditorError("project names can only use letters, numbers, _ and -")
        self.name = name
        self.char = core.check_char(char)
        self.root = PROJECTS_DIR / name
        _migrate_old_layout(self.root)
        self.dir = self.root / self.char           # one folder per character
        self.edits_dir = self.dir / "edits"
        self.work_dir = self.dir / "work"
        self.manifest_path = self.dir / "manifest.json"
        self.build_dir = core.BUILD_DIR / name / self.char
        self.built_dir = self.build_dir / "frames"
        self.preview_dir = self.build_dir / "preview"

    def manifest(self):
        with _project_lock:
            if not self.manifest_path.is_file():   # nothing edited yet; create on first save
                return {"character": self.char, "edits": {}}
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as err:
                raise EditorError(f"project manifest is unreadable: {err}") from err
            if not isinstance(data, dict) or not isinstance(data.get("edits"), dict):
                raise EditorError("project manifest is damaged")
            return data

    def _save(self, data):
        with _project_lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.manifest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.manifest_path)

    def edits(self):
        return self.manifest()["edits"]

    def edit_png(self, stem):
        return self.edits_dir / f"{stem}.png"

    # -- export
    def layouts(self, stem):
        """Canvas boxes [x, y, w, h] that 'extra space' exports of this frame were made with."""
        raw = self.manifest().get("layouts", {}).get(stem, [])
        return [tuple(int(v) for v in b) for b in raw if isinstance(b, list) and len(b) == 4]

    def _remember_layouts(self, boxes):
        with _project_lock:
            data = self.manifest()
            store = data.setdefault("layouts", {})
            for stem, box in boxes.items():
                known = [b for b in store.get(stem, []) if b != list(box)]
                store[stem] = (known + [list(box)])[-6:]
            self._save(data)

    def _current_look(self, ws, stem, slot=None):
        """(RGBA bytes, x, y, w, h) of a character frame as this mod shows it now.

        slot: draw it with that costume slot's colours, palette edits included."""
        from PIL import Image
        info = ws.frame_info(stem)
        if slot:
            crop = (self.edits().get(stem) or {}).get("crop") or [info["x"], info["y"], info["w"], info["h"]]
            with Image.open(io.BytesIO(self.render(ws, stem, slot))) as im:
                return im.convert("RGBA").tobytes(), crop[0], crop[1], im.size[0], im.size[1]
        if stem in self.edits():
            self.ensure_built(ws, stem)
            crop = self.edits()[stem].get("crop") or [info["x"], info["y"], info["w"], info["h"]]
            with Image.open(self.preview_dir / f"{stem}.png") as im:
                return im.convert("RGBA").tobytes(), crop[0], crop[1], im.size[0], im.size[1]
        with Image.open(ws.png_file(stem)) as im:
            return im.convert("RGBA").tobytes(), info["x"], info["y"], info["w"], info["h"]

    def export_move(self, ws, category, move, effects=False, masks=False, margin=0,
                    base=None, palette=False, full=False, slot=None):
        """Write the move's frames as PNGs. Never overwrites existing files.

        base: a folder the user chose (already checked with check_export_folder); the move's
        own folder is created inside it. Default: the project's work folder.
        palette=True also writes palette_<slot>.png (see palette_png) into the move's folder.

        Character frames (type 3) are always exported. Effects (type 6) only with
        effects=True, and their mask twins only with masks=True (advanced: a mask stores
        its data in RGB, so the PNG can look empty).

        margin > 0 ("extra space"): every character frame of the move is placed on ONE
        shared canvas (the area all of them cover, plus margin px on each side), at its real
        position, so the frames line up as layers and there is room to draw beyond the
        original outline (hair, hats, wigs). The project's current edits are included. The
        editor remembers the canvas, so the PNGs can be dropped back as they are.

        full=True: each character frame is placed on the game's whole 3840x2160 screen canvas
        at its real position (the same as margin, but the box is the entire canvas).

        slot: draw the frames (and the palette PNG) with that costume slot's colours, palette
        edits included, instead of the default costume's. The colour set is remembered so the
        PNGs snap back correctly when dropped. Ignored when the slot looks like the default.
        """
        from PIL import Image
        groups = ws.tree.get(category, {}).get(move)
        if groups is None:
            raise EditorError(f"unknown move {category}/{move}")
        margin = max(0, min(1024, int(margin or 0)))
        table = diff = None
        if slot:
            if slot not in ws.costume:
                raise EditorError(f"unknown costume palette: {slot}")
            diff = self._diff_from_default(ws, slot)
            if diff:
                table = self._table_with(ws, diff)
            else:
                slot = None                  # same colours as the default: nothing special to remember
        suffix = ("~fullscreen" if full else f"~space{margin}" if margin else "") + (f"~{slot}" if slot else "")
        folder = (Path(base) if base else self.work_dir) / f"{category}~{move}{suffix}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise EditorError(f"could not create {folder}: {err.strerror or err}. "
                              "Choose another export folder (the ? next to Export move).") from err
        stems = [s for names in groups.values() for s in names]
        box = None
        looks = {}
        if margin or full or slot:
            for stem in stems:
                try:
                    if ws.frame_info(stem)["type"] == 3 and not ws.frames[stem]["mask"]:
                        looks[stem] = self._current_look(ws, stem, slot)
                except (ValueError, OSError):
                    pass
            if looks and full:
                info = ws.frame_info(next(iter(looks)))
                box = (0, 0, info["canvas_w"], info["canvas_h"])
            elif looks and margin:
                info = ws.frame_info(next(iter(looks)))
                x0 = max(0, min(l[1] for l in looks.values()) - margin)
                y0 = max(0, min(l[2] for l in looks.values()) - margin)
                x1 = min(info["canvas_w"], max(l[1] + l[3] for l in looks.values()) + margin)
                y1 = min(info["canvas_h"], max(l[2] + l[4] for l in looks.values()) + margin)
                box = (x0, y0, x1 - x0, y1 - y0)
        written = kept = skipped_effects = skipped_masks = failed = 0
        boxes = {}
        for stem in stems:
            try:
                kind = ws.frame_info(stem)["type"]
                if kind == 6:
                    if ws.frames[stem]["mask"]:
                        if not masks:
                            skipped_masks += 1
                            continue
                    elif not effects:
                        skipped_effects += 1
                        continue
                target = folder / f"{stem}.png"
                if target.exists():
                    kept += 1             # may hold the user's painting
                    if box and kind == 3:
                        boxes[stem] = box
                    continue
                if box and kind == 3 and stem in looks:
                    rgba, x, y, w, h = looks[stem]
                    page = Image.new("RGBA", box[2:], (0, 0, 0, 0))
                    page.paste(Image.frombytes("RGBA", (w, h), rgba), (x - box[0], y - box[1]))
                    page.save(target)
                    boxes[stem] = box
                elif slot and kind == 3 and stem in looks:      # own size, recoloured
                    rgba, x, y, w, h = looks[stem]
                    Image.frombytes("RGBA", (w, h), rgba).save(target)
                else:
                    shutil.copyfile(ws.png_file(stem), target)
                written += 1
            except (ValueError, OSError):
                failed += 1
        if boxes:
            self._remember_layouts(boxes)
        if diff and written:
            self._remember_export_palette(diff)
        palette_file = None
        if palette and ws.default_palette is not None:
            try:
                first = move_rows(ws, category, move)
            except EditorError:
                first = []
            target = folder / f"palette_{slot or ws.default_palette['name']}.png"
            target.write_bytes(palette_png(ws, first, table))      # ours to rewrite: never a painting
            palette_file = target.name
        return {"folder": str(folder), "written": written, "kept": kept, "palette": palette_file,
                "skipped_effects": skipped_effects, "skipped_masks": skipped_masks,
                "failed": failed, "canvas": list(box) if box else None}

    # -- replace
    def _box_for(self, ws, stem, size, box=None):
        """Where on the game's screen canvas a dropped PNG of this size sits: [x, y, w, h].

        Accepted: the normal export size, an 'extra space' export made for this frame, or
        the whole 3840x2160 canvas. A box can also be given (skin packs carry their own).
        """
        info = ws.frame_info(stem)
        crop = (info["x"], info["y"], info["w"], info["h"])
        cw, ch = info["canvas_w"], info["canvas_h"]
        options = [tuple(int(v) for v in box)] if box is not None else \
            [crop, (0, 0, cw, ch)] + self.layouts(stem)
        for b in options:
            if (b[2], b[3]) != tuple(size):
                continue
            fits = (b[0] >= 0 and b[1] >= 0 and b[0] + b[2] <= cw and b[1] + b[3] <= ch
                    and b[0] <= crop[0] and b[1] <= crop[1]
                    and b[0] + b[2] >= crop[0] + crop[2] and b[1] + b[3] >= crop[1] + crop[3])
            if fits and (b == crop or info["scale"] == 1.0):
                return b
        sizes = [f"{crop[2]}×{crop[3]} (normal export)"]
        sizes += [f"{b[2]}×{b[3]} (export with extra space)" for b in self.layouts(stem)]
        sizes.append(f"{cw}×{ch} (the whole game screen)")
        raise EditorError(f"wrong canvas size: your PNG is {size[0]}×{size[1]}. This frame accepts "
                          + ", ".join(sizes) + ". Keep the size of the exported PNG.")

    # -- exports drawn with edited colours
    def _export_palettes(self):
        """Colour sets earlier exports were drawn with: [{"row.col": [r, g, b]}] vs the default."""
        raw = self.manifest().get("export_palettes", [])
        out = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict):
                out.append(item)
        return out

    def _table_with(self, ws, diff):
        table = [[list(cell) for cell in row] for row in ws.default_palette["palette"]]
        for key, rgb in diff.items():
            try:
                row, col = (int(v) for v in str(key).split("."))
                if 0 <= row < 64 and 0 <= col < 3 and len(rgb) == 3:
                    table[row][col][:3] = [max(0, min(255, int(v))) for v in rgb]
            except (ValueError, TypeError):
                continue
        return table

    def _diff_from_default(self, ws, slot):
        """{"row.col": rgb} where this slot (with the project's edits) differs from the default."""
        table = self.palette_table(ws, slot)
        base = ws.default_palette["palette"]
        return {f"{r}.{c}": list(table[r][c][:3]) for r in range(64) for c in range(3)
                if list(table[r][c][:3]) != list(base[r][c][:3])}

    def _remember_export_palette(self, diff):
        with _project_lock:
            data = self.manifest()
            known = [d for d in data.get("export_palettes", []) if d != diff]
            data["export_palettes"] = (known + [diff])[-8:]
            self._save(data)

    def _palette_of_export(self, ws, region, base, rw, rh):
        """The palette a dropped PNG was most likely drawn with.

        The default costume, or one of the colour sets earlier exports used. Pixels the artist
        did not touch still equal the game's own pixel drawn with that palette, so the palette
        that reproduces most of them is the one. Ties go to the default.
        """
        from PIL import Image, ImageChops
        default = ws.default_palette["palette"]
        extra = self._export_palettes()
        if not extra:
            return default
        painted = Image.frombytes("RGBA", (rw, rh), region)

        def matches(table):
            shown = Image.frombytes("RGBA", (rw, rh), core.render_material(base, table))
            r, g, b, a = ImageChops.difference(painted, shown).split()
            worst = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a))
            return sum(worst.histogram()[:SAME_TOLERANCE + 1])

        best, score = default, matches(default)
        for diff in reversed(extra):
            table = self._table_with(ws, diff)
            s = matches(table)
            if s > score:
                best, score = table, s
        return best

    def _snap_type3(self, ws, stem, im, box):
        """Snap a character frame painted on any accepted canvas. The frame grows when you
        painted outside its original rectangle; otherwise it keeps its exact position."""
        from PIL import Image, ImageChops
        info = ws.frame_info(stem)
        cx, cy, cw, ch = info["x"], info["y"], info["w"], info["h"]
        bx, by, bw, bh = box
        content = im.getchannel("A").point(lambda v: 255 if v >= ALPHA_CUT else 0).getbbox()
        x0, y0, x1, y1 = cx, cy, cx + cw, cy + ch
        if content:
            x0, y0 = min(x0, bx + content[0]), min(y0, by + content[1])
            x1, y1 = max(x1, bx + content[2]), max(y1, by + content[3])
        pad = DIRTY_RADIUS + 2                       # room to redraw the edge depth around changes
        rx0, ry0 = max(bx, x0 - pad), max(by, y0 - pad)
        rx1, ry1 = min(bx + bw, x1 + pad), min(by + bh, y1 + pad)
        rw, rh = rx1 - rx0, ry1 - ry0
        region = im.crop((rx0 - bx, ry0 - by, rx1 - bx, ry1 - by)).tobytes()
        original = ws.material(stem)[3]
        base = bytearray(sf.TRANSPARENT * (rw * rh))
        ox, oy = cx - rx0, cy - ry0
        for row in range(ch):
            d = ((oy + row) * rw + ox) * 4
            base[d:d + cw * 4] = original[row * cw * 4:(row + 1) * cw * 4]
        base = bytes(base)
        palette = self._palette_of_export(ws, region, base, rw, rh)
        own, others = snap_candidates(ws, stem, palette)
        material, mean_error, poor, extra = snap(
            region, own, others, base, core.render_material(base, palette),
            apply_palette(base, palette), rw, rh)
        empty = Image.new("RGBA", (rw, rh), (0, 255, 0, 0))
        used = ImageChops.difference(Image.frombytes("RGBA", (rw, rh), material), empty).getbbox()
        nx0, ny0, nx1, ny1 = cx, cy, cx + cw, cy + ch
        if used:
            nx0, ny0 = min(nx0, rx0 + used[0]), min(ny0, ry0 + used[1])
            nx1, ny1 = max(nx1, rx0 + used[2]), max(ny1, ry0 + used[3])
        nw, nh = nx1 - nx0, ny1 - ny0
        crop = bytearray(nw * nh * 4)
        for row in range(nh):
            s = ((ny0 - ry0 + row) * rw + (nx0 - rx0)) * 4
            crop[row * nw * 4:(row + 1) * nw * 4] = material[s:s + nw * 4]
        return bytes(crop), mean_error, poor, extra, (nx0, ny0, nw, nh)

    def _snap(self, ws, stem, png_bytes, box=None):
        """PNG bytes -> built .munged + preview PNG. Returns stats. Raises EditorError."""
        from PIL import Image
        info = ws.frame_info(stem)
        if info["type"] not in (3, 6):
            raise EditorError(f"unsupported image type {info['type']}")
        try:
            im = Image.open(io.BytesIO(png_bytes))
            if im.format != "PNG":
                raise EditorError("not a PNG file")
            if info["type"] == 6 and im.size != (info["w"], info["h"]):
                raise EditorError(
                    f"wrong canvas size: your PNG is {im.size[0]}×{im.size[1]} but this effect frame "
                    f"must be {info['w']}×{info['h']}. Keep the size of the exported PNG.")
            im = im.convert("RGBA")
        except EditorError:
            raise
        except Exception as err:               # PIL raises many types on bad files
            raise EditorError("could not read this file as a PNG image") from err
        orig_info = read_munged(ws.frame_path(stem))[0]
        if info["type"] == 6:                  # effects/masks: plain RGBA, stored as drawn
            rgba = im.tobytes()
            try:
                blob = type6.encode_type6(ws.frame_path(stem), rgba)
            except ValueError as err:
                raise EditorError(f"could not encode this effect frame: {err}") from err
            shown, crop = rgba, (info["x"], info["y"], info["w"], info["h"])
            stats = {"mean_error": 0.0, "far_pixels": 0, "far_examples": [], "kind": "effect"}
        else:                                  # character frames: snap to the palette
            box = self._box_for(ws, stem, im.size, box)
            material, mean_error, poor, extra, crop = self._snap_type3(ws, stem, im, box)
            blob = encode_type3_at(material, crop[2], crop[3], ws.frame_path(stem), crop[0], crop[1])
            shown = core.render_material(material, ws.default_palette["palette"])
            original = (info["x"], info["y"], info["w"], info["h"])
            stats = {"mean_error": round(mean_error, 2), "far_pixels": sum(poor.values()),
                     "far_examples": [list(c) for c, _ in poor.most_common(3)], "kind": "character",
                     **extra, "box": list(box), "crop": list(crop), "grown": tuple(crop) != original}
        width, height = crop[2], crop[3]

        self.built_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        out = self.built_dir / f"{stem}.munged"
        tmp = self.built_dir / f"{stem}.tmp"
        tmp.write_bytes(blob)
        try:
            new_info = read_munged(tmp)[0]     # must parse, and keep the frame's identity
        except ValueError as err:
            tmp.unlink(missing_ok=True)
            raise EditorError(f"internal error: encoded frame is invalid ({err})") from err
        for key in ("canvas_width", "canvas_height", "asset_key", "flags", "source", "image_type"):
            if new_info[key] != orig_info[key]:
                tmp.unlink(missing_ok=True)
                raise EditorError(f"internal error: encoded frame lost its {key}")
        if (new_info["crop_x"], new_info["crop_y"], new_info["crop_width"], new_info["crop_height"]) != tuple(crop):
            tmp.unlink(missing_ok=True)
            raise EditorError("internal error: encoded frame has the wrong position")
        tmp.replace(out)
        Image.frombytes("RGBA", (width, height), bytes(shown)).save(self.preview_dir / f"{stem}.png")
        (self.built_dir / f"{stem}.sha").write_text(self._stamp(ws, png_bytes), encoding="ascii")
        return stats

    def replace(self, ws, filename, png_bytes, box=None):
        name = Path(str(filename).replace("\\", "/")).name
        stem = name[:-4] if name.lower().endswith(".png") else name
        if stem not in ws.frames:
            owner = stem.split("~")[1] if stem.startswith("sprites~") and stem.count("~") >= 2 else None
            if owner and owner != self.char:
                raise EditorError(f"this frame belongs to {owner}, not {self.char} — "
                                  f"switch to the {owner} tab and drop it there")
            raise EditorError("no frame with this name — keep the exported file name")
        if len(png_bytes) > MAX_PNG_BYTES:
            raise EditorError("file is too large")
        stats = self._snap(ws, stem, png_bytes, box)
        with _project_lock:
            self.edits_dir.mkdir(parents=True, exist_ok=True)
            self.edit_png(stem).write_bytes(png_bytes)
            data = self.manifest()
            data["edits"][stem] = {**stats,
                                   "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
                                   "palette": ws.default_palette["name"],
                                   "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save(data)
        return {"name": stem, **data["edits"][stem]}

    def revert(self, stem):
        if not self.revert_many([stem]):
            raise EditorError("that frame has no edit")

    def revert_many(self, stems):
        """Remove the edits of several frames in one manifest write. Returns how many."""
        with _project_lock:
            data = self.manifest()
            gone = [s for s in dict.fromkeys(stems) if isinstance(s, str) and s in data["edits"]]
            for stem in gone:
                del data["edits"][stem]
            if gone:
                self._save(data)
            for stem in gone:
                for path in (self.edit_png(stem), self.built_dir / f"{stem}.munged",
                             self.built_dir / f"{stem}.sha", self.preview_dir / f"{stem}.png"):
                    path.unlink(missing_ok=True)
            return len(gone)

    def ensure_built(self, ws, stem):
        """Built .munged for an edit, re-snapping if it is missing or the PNG changed."""
        if stem not in self.edits() or stem not in ws.frames:
            raise EditorError(f"no edit for {stem}")
        png = self.edit_png(stem)
        if not png.is_file():
            raise EditorError(f"edited PNG is missing: {png.name}")
        data = png.read_bytes()
        out = self.built_dir / f"{stem}.munged"
        stamp = self.built_dir / f"{stem}.sha"
        fresh = (out.is_file() and (self.preview_dir / f"{stem}.png").is_file()
                 and stamp.is_file() and stamp.read_text(encoding="ascii") == self._stamp(ws, data))
        if not fresh:
            with _build_locks.setdefault((self.char, stem), threading.Lock()):
                # another request may have rebuilt it while this one waited
                if (out.is_file() and stamp.is_file()
                        and stamp.read_text(encoding="ascii") == self._stamp(ws, data)):
                    return out
                stats = self._snap(ws, stem, data, self.edits()[stem].get("box"))
            if "crop" in stats:                # keep the manifest's position in step
                with _project_lock:
                    manifest = self.manifest()
                    if stem in manifest["edits"]:
                        manifest["edits"][stem].update(crop=stats["crop"], box=stats["box"],
                                                       grown=stats["grown"])
                        self._save(manifest)
        return out

    @staticmethod
    def _stamp(ws, png_bytes):
        """Identifies what a built frame was made from: the PNG AND the game's pak it was
        built against, so a game update triggers a re-snap instead of reusing old frames."""
        return hashlib.sha256(png_bytes).hexdigest() + ":" + ws.digest[:16] + ":" + SNAP_VERSION

    def missing(self, ws):
        """Edited frames that no longer exist in the game's pak (removed by a game update)."""
        return [stem for stem in sorted(self.edits()) if stem not in ws.frames]

    def preview_png(self, ws, stem):
        self.ensure_built(ws, stem)
        return self.preview_dir / f"{stem}.png"

    def edited_summary(self, ws):
        """Everything this project changes for the character, for the 'Edited' view."""
        frames = []
        for stem, edit in sorted(self.edits().items(), key=lambda kv: core.natural_key(kv[0])):
            entry = ws.frames.get(stem)
            if entry:
                frames.append({**entry, "edit": edit})
        return {
            "frames": frames,
            "missing": self.missing(ws),
            "palettes": [{"name": name, "colors": len(cells)}
                         for name, cells in sorted(self.palette_edits().items())],
            "folders": {"edits": str(self.edits_dir), "work": str(self.work_dir),
                        "project": str(self.root)},
        }

    def folder(self, which):
        """A folder the page may open in Explorer (fixed choices only)."""
        path = {"edits": self.edits_dir, "work": self.work_dir, "project": self.root,
                "exports": EXPORT_DIR}.get(which)
        if path is None:
            raise EditorError("unknown folder")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def overlay(self, ws):
        out = {f"srcdata/munged/frames/{stem}.munged": self.ensure_built(ws, stem)
               for stem in sorted(self.edits()) if stem in ws.frames}
        sprbin = self.patched_sprbin(ws)
        if sprbin:
            out[f"srcdata/munged/scripts/sprites~{self.char}~{self.char}.sprbin"] = sprbin
        return out

    # -- palettes
    def palette_edits(self):
        """{palette name: {(row, col): (r, g, b)}} — validated copy of the manifest."""
        raw = self.manifest().get("palettes", {})
        out = {}
        if not isinstance(raw, dict):
            return out
        for name, rows in raw.items():
            cells = {}
            if isinstance(rows, dict):
                for row, cols in rows.items():
                    if not isinstance(cols, dict):
                        continue
                    for col, rgb in cols.items():
                        try:
                            key = (int(row), int(col))
                            color = tuple(int(v) for v in rgb)
                        except (TypeError, ValueError):
                            continue
                        if (0 <= key[0] < 64 and 0 <= key[1] < 3 and len(color) == 3
                                and all(0 <= v <= 255 for v in color)):
                            cells[key] = color
            if cells:
                out[name] = cells
        return out

    def palette_color_count(self):
        return sum(len(cells) for cells in self.palette_edits().values())

    def palette_table(self, ws, name):
        """The palette as the mod will ship it: 64 rows x 4 [r, g, b, a] lists."""
        record = ws.costume.get(name)
        if record is None:
            raise EditorError(f"unknown costume palette: {name}")
        table = [[list(cell) for cell in row] for row in record["palette"]]
        for (row, col), rgb in self.palette_edits().get(name, {}).items():
            table[row][col][:3] = rgb          # alpha and column 3 (shader data) untouched
        return table

    def set_color(self, ws, name, row, col, rgb):
        """rgb=None puts the original color back."""
        if name not in ws.costume:
            raise EditorError(f"unknown costume palette: {name}")
        if not (isinstance(row, int) and isinstance(col, int) and 0 <= row < 64 and 0 <= col < 3):
            raise EditorError("row must be 0-63 and column 0-2")
        if rgb is not None:
            try:
                rgb = [int(v) for v in rgb]
            except (TypeError, ValueError) as err:
                raise EditorError("invalid color") from err
            if len(rgb) != 3 or not all(0 <= v <= 255 for v in rgb):
                raise EditorError("invalid color")
            if tuple(rgb) == tuple(ws.costume[name]["palette"][row][col][:3]):
                rgb = None                     # same as the original = no edit
        with _project_lock:
            data = self.manifest()
            palettes = data.setdefault("palettes", {})
            cols = palettes.setdefault(name, {}).setdefault(str(row), {})
            if rgb is None:
                cols.pop(str(col), None)
            else:
                cols[str(col)] = rgb
            if not cols:
                del palettes[name][str(row)]
            if not palettes[name]:
                del palettes[name]
            self._save(data)

    def set_colors(self, ws, name, cells):
        """Bulk version of set_color. cells: {(row, col): (r, g, b)}. One manifest write."""
        record = ws.costume.get(name)
        if record is None:
            raise EditorError(f"unknown costume palette: {name}")
        with _project_lock:
            data = self.manifest()
            palettes = data.setdefault("palettes", {})
            slot = palettes.setdefault(name, {})
            for (row, col), rgb in cells.items():
                if not (0 <= row < 64 and 0 <= col < 3) or len(rgb) != 3 \
                        or not all(isinstance(v, int) and 0 <= v <= 255 for v in rgb):
                    continue
                cols = slot.setdefault(str(row), {})
                if tuple(rgb) == tuple(record["palette"][row][col][:3]):
                    cols.pop(str(col), None)       # same as the original = no edit
                else:
                    cols[str(col)] = list(rgb)
                if not cols:
                    del slot[str(row)]
            if not slot:
                del palettes[name]
            self._save(data)

    def reset_palette(self, name):
        with _project_lock:
            data = self.manifest()
            if isinstance(data.get("palettes"), dict):
                data["palettes"].pop(name, None)
            self._save(data)

    def patched_sprbin(self, ws):
        """Path of a .sprbin with this project's palette edits, or None if there are none."""
        edits = {name: cells for name, cells in self.palette_edits().items()
                 if name in ws.costume}
        if not edits:
            return None
        original = core.sprbin_path(self.char, ws.root).read_bytes()
        patched = write_palettes(original, ws, edits)
        if len(patched) != len(original):
            raise EditorError("internal error: palette patch changed the file size")
        out = self.build_dir / f"sprites~{self.char}~{self.char}.sprbin"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(patched)
        return out

    def render(self, ws, stem, palette_name, rows=False, thumb=False, override=None):
        """PNG bytes of a frame drawn with this project's version of a costume palette.

        override: {(row, col): (r, g, b)} colours to try out for this picture only (the
        colour picker's live preview); nothing is saved.

        rows=True gives core.row_map() instead: which palette row/shade paints each pixel.
        thumb=True shrinks it to fit 192x192, like the cached timeline thumbnails.
        """
        from PIL import Image
        edit = self.edits().get(stem)
        key = small = None
        if thumb and not rows:                 # thumbnails: recolour a shrunken copy (much less work)
            key = (str(ws.root), stem, edit.get("png_sha256") if edit else None)
            with _small_lock:
                small = _small_materials.get(key)
        if small:
            image_type, width, height, image = small
        elif edit:                             # show the user's replaced frame, not the original
            info, tiles, chunks = read_munged(self.ensure_built(ws, stem))
            atlas = b"".join(decompress_lz4(data, size) for size, data in chunks)
            width, height, image = unpack_tiles(info, tiles, atlas)
            image_type = info["image_type"]
        else:
            image_type, width, height, image = ws.material(stem)
        if key and not small and image_type == 3:
            tiny = Image.frombytes("RGBA", (width, height), bytes(image))
            tiny.thumbnail((SMALL_BOX, SMALL_BOX), Image.NEAREST)   # material bytes must not be blended
            width, height = tiny.size
            image = tiny.tobytes()
            with _small_lock:
                if len(_small_materials) >= 400:
                    _small_materials.pop(next(iter(_small_materials)))
                _small_materials[key] = (image_type, width, height, image)
        if image_type == 3:
            draw = core.row_map if rows else core.render_material
            table = self.palette_table(ws, palette_name)
            for (row, col), rgb in (override or {}).items():
                table[row][col][:3] = rgb
            image = draw(image, table)
        elif rows:
            raise EditorError("only character frames use the costume palette")
        out = Image.frombytes("RGBA", (width, height), image)
        if thumb and not rows:
            out.thumbnail((192, 192), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", compress_level=1)
        return buf.getvalue()


def palette_png(ws, first_rows=(), table=None):
    """PNG bytes: every colour a painted character frame may use, as big pickable swatches.

    Uses the DEFAULT costume's original colours, because that is what exported frames are
    drawn with and what snap() matches painted pixels against (the game then recolours per
    costume). Each swatch is exactly what a pure shadow / base / light pixel renders as, so
    painting with it re-imports with no error. One line per palette row (number, shadow,
    base, light); the rows in first_rows (the exported move's) come first, then the rest.
    """
    from PIL import Image, ImageDraw
    palette = table or ws.default_palette["palette"]      # table: a recoloured costume (see export_move)
    lines = {}
    for row in allowed_rows(ws):
        cells = [core.render_material(bytes((row, 255, blue, 255)), palette) for blue in sf.SHADES]
        if all(px[3] == 255 for px in cells):          # see-through rows cannot be picked reliably
            lines[row] = [tuple(px[:3]) for px in cells]
    first = [r for r in first_rows if r in lines]
    rest = [r for r in lines if r not in first]
    sw, gap, label, per_col, top = 32, 4, 30, 16, 22
    line_w = label + 3 * sw + 2 * gap
    columns = []                                        # [(title, [rows])], 16 lines per column
    for title, rows in (("this move", first), ("other rows" if first else "rows", rest)):
        for i in range(0, len(rows), per_col):
            columns.append((title if i == 0 else "", rows[i:i + per_col]))
    columns.append(("outline", [None]))
    width = 12 + len(columns) * (line_w + 20) - 8
    height = top + 12 + per_col * (sw + gap) + 8
    im = Image.new("RGBA", (width, height), (38, 41, 46, 255))
    pen = ImageDraw.Draw(im)
    for c, (title, rows) in enumerate(columns):
        x0 = 12 + c * (line_w + 20)
        pen.text((x0, 8), title, fill=(179, 184, 193, 255))
        for i, row in enumerate(rows):
            y = top + 12 + i * (sw + gap)
            if row is None:                             # the black line colour
                pen.rectangle([x0 + label, y, x0 + label + sw - 1, y + sw - 1], fill=(0, 0, 0, 255))
                continue
            pen.text((x0, y + 10), str(row), fill=(228, 230, 234, 255))
            for k, rgb in enumerate(lines[row]):
                x = x0 + label + k * (sw + gap)
                pen.rectangle([x, y, x + sw - 1, y + sw - 1], fill=rgb + (255,))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def check_export_folder(text, game_dir):
    """A folder the user typed or picked for exported PNGs -> resolved Path, or EditorError.

    Must exist. Never the game's folder, the backups, one_pak or a project's edits folder.
    """
    text = str(text or "").strip().strip('"')
    path = Path(text)
    if not text or not path.is_absolute() or not path.is_dir():
        raise EditorError(f"that folder does not exist: {text or '(empty)'}")
    path = path.resolve()
    game = Path(game_dir)
    if game.name.lower() == "data_packages":
        game = game.parent
    for bad, what in ((game, "the game's folder"), (core.BACKUP_DIR, "the backup folder")):
        try:
            bad = bad.resolve()
        except OSError:
            continue
        if path == bad or bad in path.parents:
            raise EditorError(f"pick a folder outside {what} ({bad})")
    parts = [p.lower() for p in path.parts]
    if "one_pak" in parts or "backup" in parts:
        raise EditorError("pick a folder that is not inside a backup or one_pak folder")
    projects = PROJECTS_DIR.resolve()
    if (path == projects or projects in path.parents) and "edits" in parts:
        raise EditorError("that is the folder the mod is built from; pick another one")
    return path


def write_palettes(data, ws, edits):
    """Rewrite every costume palette record of a .sprbin from its parsed table.

    edits: {palette name: {(row, col): (r, g, b)}}. Only RGB of columns 0-2 can change.
    With no edits the result must equal the input byte for byte (see sprbin_roundtrip).
    """
    out = bytearray(data)
    for name, record in ws.costume.items():
        offset = record["offset"]
        if not (0 <= offset and offset + 1024 <= len(out)):
            raise EditorError(f"palette {name} lies outside the file")
        table = [[list(cell) for cell in row] for row in record["palette"]]
        for (row, col), rgb in edits.get(name, {}).items():
            table[row][col][:3] = rgb
        blob = bytes(v for row in table for cell in row for v in cell)
        if len(blob) != 1024:
            raise EditorError(f"palette {name} did not serialize to 1024 bytes")
        out[offset:offset + 1024] = blob
    return bytes(out)


def sprbin_roundtrip(ws):
    """Milestone 5 check: writing all palettes back unchanged gives the identical file."""
    original = core.sprbin_path(ws.char, ws.root).read_bytes()
    return write_palettes(original, ws, {}) == original, len(ws.costume)


def move_rows(ws, category, move):
    """Palette rows the move's character frames really use (cached)."""
    key = (category, move)
    if key not in ws._move_rows:
        groups = ws.tree.get(category, {}).get(move)
        if groups is None:
            raise EditorError(f"unknown move {category}/{move}")
        paths = [ws.frames_dir / f"{stem}.munged" for names in groups.values() for stem in names
                 if not ws.frames[stem]["mask"]]
        try:
            ws._move_rows[key] = sf.rows_used(paths, 30)
        except (ValueError, OSError) as err:
            raise EditorError(f"could not read the move's frames: {err}") from err
    return ws._move_rows[key]


# ----------------------------------------------------------------- install / restore

def _load_installed():
    try:
        data = json.loads(INSTALLED_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def installed_info(char):
    info = _load_installed().get(char)
    return info if isinstance(info, dict) else None


def _set_installed(char, info):
    data = _load_installed()
    if info is None:
        data.pop(char, None)
    else:
        data[char] = info
    INSTALLED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _copy_into_game(src, dst):
    """Copy next to the target, then swap it in, so a failure never leaves half a pak."""
    tmp = dst.with_name(dst.name + ".skineditor.tmp")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except PermissionError as err:
        raise EditorError(
            "Windows would not let me replace the pak. Close the game completely "
            "and try again.") from err
    except OSError as err:
        raise EditorError(f"could not copy into the game folder: {err}") from err
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def install(project, ws, game_dir):
    """Build the mod and put it in the game folder. Refuses without a verified backup."""
    char = project.char
    original = core.verify_backup(char)               # raises -> no install
    target = Path(game_dir) / f"{char}.pak"
    if not target.is_file():
        raise EditorError(f"{target} not found — check the game folder setting")
    current = core.sha256_file(target)
    ours = (installed_info(char) or {}).get("hash")
    if current not in (original, ours):
        raise EditorError(
            "the pak in the game folder is neither the original nor one this editor "
            "installed (the game may have updated). Use 'Scan game folder' to refresh "
            "the backup, or verify game files in Steam, then try again.")
    overlay = project.overlay(ws)
    if not overlay:
        raise EditorError("this project has no edits yet — nothing to install")
    built = project.build_dir / f"{char}.pak"
    digest, count = core.build_pak(char, built, overlay)
    try:
        PakArchive(built)                             # the result must parse cleanly
    except PakError as err:
        raise EditorError(f"built pak failed its self-check: {err}") from err
    _copy_into_game(built, target)
    if core.sha256_file(target) != digest:
        raise EditorError("the installed pak does not match the build — use Restore original")
    skipped = len(project.missing(ws))
    frames, colors = len(project.edits()) - skipped, project.palette_color_count()
    _set_installed(char, {"hash": digest, "project": project.name, "edits": frames,
                          "colors": colors, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    built.unlink(missing_ok=True)
    return {"hash": digest, "files": count, "edits": frames, "colors": colors,
            "skipped": skipped}


def restore(char, game_dir):
    """Copy the verified backup back into the game folder and confirm the hash."""
    original = core.verify_backup(char)
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise EditorError(f"game folder not found: {game_dir}")
    target = game_dir / f"{char}.pak"
    current = core.sha256_file(target) if target.is_file() else None
    if current != original:
        if current is not None and current != (installed_info(char) or {}).get("hash"):
            raise EditorError(
                "the game's pak is neither the original nor this editor's mod — the game "
                "has probably updated. Restoring would replace it with an older file, so I "
                "did not. Use 'Scan game folder' to refresh the backup, or verify game "
                "files in Steam.")
        _copy_into_game(core.backup_path(char), target)
    if core.sha256_file(target) != original:
        raise EditorError("restore finished but the hash does not match the backup")
    _set_installed(char, None)
    return {"hash": original}
