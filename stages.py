"""Game stages as backdrops for the preview.

Reads the stage paks straight from the game folder (read-only), composes the stage's
background layers from its .lvl scene script, and caches the result as a PNG. Background
characters (NPC sprites) and animated effects are left out; only the painted layers are
used. In-game character lighting is not reproduced.

.lvl facts used here (plain text, verified on air_temple_island):
  StageSize W,H,D        ImageFloorY y        Tags ... FightStage ...
  Layer png/Name.png     LayerPos x,y,z       LayerScale sx sy
  ParallaxPercents px py Hidden
"png/Name.png" resolves to data/levels/<stage>/dds/<name>.dds inside the pak.
Most layer images are cropped to their painted area; <name>.infobg next to the .dds says where
the crop sits in the canvas it was cut from ("Off x y" inside "Size w h", usually the full stage).
LayerPos places that canvas, so a layer's stage position is Off * LayerScale + LayerPos.
"""
import io
import json
import re
import threading
from pathlib import Path

import core
from core import EditorError
from pak_extract import PakArchive, PakError

CAMERA_W, CAMERA_H = 3840, 2160          # the game's screen space (same as the sprite canvas)
FLOOR_IN_VIEW = 0.86                     # where the floor line sits in the preview, top to bottom
OUT_SCALE = 0.5                          # cached backdrop is 1920 x 1080
MAX_LVL_BYTES = 2 * 1024 * 1024
MAX_DDS_BYTES = 256 * 1024 * 1024
MAX_INFO_BYTES = 4096
RENDER_VERSION = "r4"                     # bump to rebuild cached backdrops

# Stages left out of the Backdrop list because their composite looks wrong. With the .infobg
# offsets applied, all ten fight stages compose correctly (checked by eye, 2026-11-12). Floor
# planes the game tilts in 3D (ParallaxSkew) are drawn flat, which reads fine from the front.
HIDDEN_STAGES = set()

HOME = {                                 # a character's home stage, used as the default
    "aang": "airtemple", "aang_avchar": "avatar_road", "zaheer": "airtemple",
    "zuko": "fire_throne", "ozai": "fire_throne", "ozai_boss": "fire_throne",
    "azula": "fire_throne_azula", "azula_super": "fire_throne_azula",
    "korra": "republiccity_streets", "korra_nightmare": "avatar_road",
    "korra_nightmare_boss": "avatar_road", "wanshitong": "wanshitong_lvl",
    "katara": "watertribe", "sokka": "watertribe", "sokka_super": "watertribe",
    "toph": "ba_sing_se", "kyoshi": "air_temple_island",
}
NAMES = {
    "air_temple_island": "Air Temple Island", "airtemple": "Air Temple", "avatar_road": "Avatar Road",
    "avatar_road_corrupted": "Avatar Road (corrupted)", "ba_sing_se": "Ba Sing Se",
    "ba_sing_se_hollow": "Ba Sing Se (hollow)", "ba_sing_se_ruins": "Ba Sing Se (ruins)",
    "fire_throne": "Fire Throne", "fire_throne_azula": "Fire Throne (Azula)",
    "fire_throne_hollow": "Fire Throne (hollow)", "republiccity_streets": "Republic City",
    "republiccity_streets_night": "Republic City (night)", "republiccity_streets_ruins": "Republic City (ruins)",
    "spiritwilds": "Spirit Wilds", "wanshitong_lvl": "Wan Shi Tong's Library",
    "wanshitong_hollow": "Wan Shi Tong's Library (hollow)", "watertribe": "Water Tribe",
    "watertribe_hollow": "Water Tribe (hollow)", "watertribe_ruins": "Water Tribe (ruins)",
    "wu_long_forest": "Wu Long Forest",
}

_lock = threading.Lock()
_list_cache = {}


def _lvl_of(entries):
    for e in entries:
        p = e.path.lower()
        if p.startswith("data/levels/") and p.endswith(".lvl") and "defaults" not in p:
            return e
    return None


def _read(path, entry, limit):
    if entry.size > limit:
        raise EditorError(f"{entry.path} is unreasonably large")
    with Path(path).open("rb") as f:
        f.seek(entry.offset)
        return f.read(entry.size)


def list_stages(game_dir):
    """[{id, name}] for every fight stage pak in the game folder (sorted by name)."""
    folder = Path(game_dir)
    if not folder.is_dir():
        return []
    out = []
    for pak in sorted(folder.glob("*.pak")):
        try:
            st = pak.stat()
        except OSError:
            continue
        key = (str(pak).lower(), st.st_size, st.st_mtime_ns)
        if key not in _list_cache:
            info = None
            try:
                entries = PakArchive(pak).entries
                lvl = _lvl_of(entries)
                if lvl is not None and any(e.path.lower().endswith(".dds") for e in entries):
                    text = _read(pak, lvl, MAX_LVL_BYTES).decode("utf-8", "replace")
                    tags = re.search(r"^Tags:?\s+(.*)$", text, re.M)
                    if tags and "FightStage" in tags.group(1).split():
                        info = {"id": pak.stem.lower(), "name": NAMES.get(pak.stem.lower(),
                                pak.stem.replace("_", " ").title())}
            except (PakError, OSError, EditorError):
                info = None
            _list_cache[key] = info
        if _list_cache[key] and _list_cache[key]["id"] not in HIDDEN_STAGES:
            out.append(dict(_list_cache[key]))
    return sorted(out, key=lambda s: s["name"])


def home_stage(char, stages):
    ids = {s["id"] for s in stages}
    want = HOME.get(char)
    return want if want in ids else (stages[0]["id"] if stages else None)


def _infobg(pak, entry):
    """(x, y) of a cropped layer image inside the canvas it was cut from, from its .infobg
    ("Off x y" / "Size w h"); (0, 0) if there is none. LayerPos places that canvas."""
    if entry is None:
        return 0, 0
    try:
        text = _read(pak, entry, MAX_INFO_BYTES).decode("ascii", "replace")
    except (OSError, EditorError):
        return 0, 0
    off = re.search(r"^Off\s+(-?\d+)\s+(-?\d+)", text, re.M)
    if not off:
        return 0, 0
    x, y = int(off.group(1)), int(off.group(2))
    return (x, y) if abs(x) <= 20000 and abs(y) <= 20000 else (0, 0)


def _parse_lvl(text):
    """(stage_w, stage_h, floor_y, [layer dicts in drawing order])."""
    size = re.search(r"^StageSize\s+(-?\d+),\s*(-?\d+)", text, re.M)
    floor = re.search(r"^ImageFloorY\s+(-?\d+)", text, re.M)
    stage_w, stage_h = (int(size.group(1)), int(size.group(2))) if size else (5330, 2998)
    floor_y = int(floor.group(1)) if floor else int(stage_h * 0.83)
    layers, cur, foreground = [], None, False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("BeginForeground"):
            foreground, cur = True, None      # everything after this is drawn in front of fighters
        elif line.startswith("Layer "):
            cur = {"file": line.split(None, 1)[1].strip(), "x": 0.0, "y": 0.0, "sx": 1.0, "sy": 1.0,
                   "px": 100.0, "py": 100.0, "hidden": False, "fg": foreground, "alpha": 1.0}
            layers.append(cur)
        elif line.startswith(("Sprite ", "Area")):
            cur = None
        elif cur is not None:
            parts = re.split(r"[\s,]+", line)
            try:
                if parts[0] == "LayerPos":
                    cur["x"], cur["y"] = float(parts[1]), float(parts[2])
                elif parts[0] == "LayerScale":
                    cur["sx"], cur["sy"] = float(parts[1]), float(parts[2])
                elif parts[0] == "ParallaxPercents":
                    cur["px"], cur["py"] = float(parts[1]), float(parts[2])
                elif parts[0] == "Hidden":
                    cur["hidden"] = True
                elif parts[0] in ("ParallaxSkew", "SkewFromWorldCenter"):
                    cur["skew"] = True        # a floor plane the game tilts in 3D; drawn flat here
                elif parts[0] == "AlphaMult":
                    cur["alpha"] = max(0.0, min(1.0, float(parts[1])))
            except (IndexError, ValueError):
                pass
    return stage_w, stage_h, floor_y, layers


def render(game_dir, stage_id):
    """(png path, meta) for a stage backdrop. meta: width, height, floor_y (px in the PNG),
    center_x, scale (PNG px per game px). Composed once, then cached."""
    from PIL import Image
    if not re.fullmatch(r"[a-z0-9_]{1,60}", stage_id or ""):
        raise EditorError("unknown stage")
    if stage_id not in {s["id"] for s in list_stages(game_dir)}:
        raise EditorError(f"no stage named {stage_id} in the game folder")
    pak = Path(game_dir) / f"{stage_id}.pak"
    st = pak.stat()
    folder = core.CACHE_DIR / "stages"
    stamp = f"{stage_id}-{st.st_size}-{st.st_mtime_ns}-{RENDER_VERSION}"
    png, meta_path = folder / f"{stamp}.png", folder / f"{stamp}.json"
    with _lock:
        if png.is_file() and meta_path.is_file():
            return png, json.loads(meta_path.read_text(encoding="utf-8"))
        entries = PakArchive(pak).entries
        lvl = _lvl_of(entries)
        text = _read(pak, lvl, MAX_LVL_BYTES).decode("utf-8", "replace")
        stage_w, stage_h, floor_y, layers = _parse_lvl(text)
        dds = {e.path.rsplit("/", 1)[-1].lower(): e for e in entries if e.path.lower().endswith(".dds")}
        info = {e.path.rsplit("/", 1)[-1].lower(): e for e in entries if e.path.lower().endswith(".infobg")}
        # camera: centred on the stage, floor line near the bottom of the view
        left = max(0, min(stage_w - CAMERA_W, stage_w // 2 - CAMERA_W // 2))
        top = max(0, min(stage_h - CAMERA_H, round(floor_y - CAMERA_H * FLOOR_IN_VIEW)))
        view = Image.new("RGBA", (CAMERA_W, CAMERA_H), (20, 22, 26, 255))
        drawn = 0
        for layer in layers:
            # hidden layers and the foreground (mist, front plants) are left out
            if layer["hidden"] or layer["fg"]:
                continue
            name = layer["file"].replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() + ".dds"
            entry = dds.get(name)
            if entry is None:
                continue
            off_x, off_y = _infobg(pak, info.get(name[:-4] + ".infobg"))
            try:
                with Image.open(io.BytesIO(_read(pak, entry, MAX_DDS_BYTES))) as im:
                    im = im.convert("RGBA")
                    w, h = max(1, round(im.width * layer["sx"])), max(1, round(im.height * layer["sy"]))
                    if (w, h) != im.size:
                        im = im.resize((w, h), Image.LANCZOS)
                    if layer["alpha"] < 1.0:
                        im.putalpha(im.getchannel("A").point(lambda v, k=layer["alpha"]: round(v * k)))
                    # a layer moves at ParallaxPercents of the camera, so its screen position is
                    # its position minus that share of the camera offset
                    x = round(off_x * layer["sx"] + layer["x"] - left * layer["px"] / 100.0)
                    y = round(off_y * layer["sy"] + layer["y"] - top * layer["py"] / 100.0)
                    # crop the part that falls on screen, then blend it (keeps soft edges and mist)
                    src = (max(0, -x), max(0, -y), min(im.width, CAMERA_W - x), min(im.height, CAMERA_H - y))
                    if src[2] > src[0] and src[3] > src[1]:
                        view.alpha_composite(im, (max(0, x), max(0, y)), src)
                    drawn += 1
            except Exception:
                continue                     # an undecodable layer is skipped, never fatal
        if not drawn:
            raise EditorError("this stage's backgrounds could not be read")
        view = view.convert("RGB")
        view = view.resize((round(CAMERA_W * OUT_SCALE), round(CAMERA_H * OUT_SCALE)), Image.LANCZOS)
        folder.mkdir(parents=True, exist_ok=True)
        view.save(png, optimize=True)
        meta = {"width": view.width, "height": view.height, "floor_y": round((floor_y - top) * OUT_SCALE),
                "center_x": view.width // 2, "scale": OUT_SCALE, "layers": drawn}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return png, meta


def anchor(ws):
    """Where the character's feet are in its own sprite canvas: (x, y), from the idle frames."""
    frames = [s for s, e in ws.frames.items() if e["category"] == "idle" and e["kind"] == "frames"
              and not e["mask"]]
    if not frames:
        frames = [s for s, e in ws.frames.items() if e["kind"] == "frames" and not e["mask"]]
    boxes = []
    for s in frames[:60]:
        try:
            i = ws.frame_info(s)
            if i["type"] == 3:
                boxes.append(i)
        except (ValueError, OSError):
            pass
    if not boxes:
        return {"x": 1920, "y": 1000}
    bottoms = sorted(b["y"] + b["h"] for b in boxes)
    centers = sorted(b["x"] + b["w"] / 2 for b in boxes)
    return {"x": round(centers[len(centers) // 2]), "y": bottoms[len(bottoms) // 2]}
