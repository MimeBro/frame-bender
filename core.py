"""Core logic for the Avatar Legends skin editor (no web code in here).

Everything that touches files lives in this module:
  - verifying the untouched backup pak (SHA256)
  - extracting it once into skin-editor/cache/
  - listing moves/frames parsed from the frame filenames
  - decoding frames to PNG (cached)
  - rebuilding a pak from the cache (+ optional replaced files)

Nothing in here writes to the game folder. backup/ is only ever added to (a first backup,
or a game update that moves the old backup into backup/previous/); nothing is deleted.
"""
import hashlib
import io
import json
from array import array
import math
import os
import re
import shutil
import struct
import sys
import threading
from pathlib import Path

from paths import APP_DIR, DATA_DIR, FROZEN  # noqa: E402,F401  (see paths.py)
import paths  # noqa: E402
import toolsfetch  # noqa: E402


def _find_tools():
    """Folder with the avatar-legends-tools scripts: a git submodule, a folder you copied
    them into, or a verified download made from the setup screen. Never downloads by itself."""
    folder = paths.find_tools()
    if folder is None:
        sys.exit(toolsfetch.missing_report(paths.tool_dirs(), paths.TOOLS_DOWNLOAD_DIR))
    return folder


def _find_backup_dir():
    env = os.environ.get("SKIN_EDITOR_BACKUP")
    if env:
        return Path(env)
    mine = DATA_DIR / "backup"
    legacy = APP_DIR.parent / "backup"      # the author's original workspace layout
    if DATA_DIR == APP_DIR and not mine.exists() and legacy.is_dir():
        return legacy
    return mine


TOOLS = _find_tools()
BACKUP_DIR = _find_backup_dir()
CACHE_DIR = DATA_DIR / "cache"
BUILD_DIR = DATA_DIR / "build"
CONFIG_PATH = DATA_DIR / "config.json"
ORIGINALS_PATH = DATA_DIR / "originals.json"

# Our own scripts (snap_frame.py) are found first; the tools are found after them.
sys.path.insert(0, str(APP_DIR))
sys.path.append(str(TOOLS))
from munged_extract import (apply_palette, decompress_lz4, default_palette_record,  # noqa: E402
                            find_palette_record, is_selection_palette, read_munged,
                            read_palette_records_data, shader_rgb, unpack_tiles)
from pak_extract import PakArchive, PakError  # noqa: E402

DEFAULT_GAME_DIR = (r"C:\Program Files (x86)\Steam\steamapps\common"
                    r"\Avatar Legends The Fighting Game\data_packages")

# Nothing is hard-coded: the first time a character is backed up, the hash of that pak is
# remembered in originals.json and every later check compares against it. The game can
# update its paks, so a built-in list would be wrong for other people.

# Limits for untrusted binary data.
MAX_MUNGED_BYTES = 64 * 1024 * 1024
MAX_RAW_BYTES = 256 * 1024 * 1024
MAX_DIM = 8192
MAX_SPRBIN_BYTES = 256 * 1024 * 1024

# pak layout (same as pak_pack.py, which rebuilds byte-identical paks)
PAK_DATA_START = 0x1000
PAK_ALIGN = 0x1000
PAK_NAME_LEN = 248

CHAR_RE = re.compile(r"^[a-z0-9_]{1,40}$")


class EditorError(Exception):
    """An error with a message that is safe and useful to show the user."""


# ----------------------------------------------------------------- drawing character frames

def _fill_alpha(red, blue, palette):
    """How opaque the FILL of a material pixel is (0..1), from the palette's alpha channel.

    Same row / blend-row / shade-column selection as apply_palette(), applied to alpha.
    Palette row 0 is (0,0,0,0) in every costume palette: "no fill". The soft outer edge of
    the black outline lives on row 0, so there only the line itself is visible.
    """
    scaled = red * 4.0 / 255.0
    band = math.floor(scaled)
    row = min(63, int((scaled - band) * 64.0))
    blend = band / 7.0
    blend_row = max(0, min(63, int(shader_rgb(palette[row][3])[1])))
    shade = blue / 255.0
    shade = (shade - 0.5) / 0.6 if shade <= 0.8 else 0.5 + (shade - 0.8) / 0.3
    shade = max(0.0, min(1.0, shade))
    column = 0 if shade <= 0.5 else 1
    amount = shade * 2.0 - column
    values = []
    for r in (row, blend_row):
        first, second = palette[r][column][3], palette[r][column + 1][3]
        values.append(first + (second - first) * amount)
    return (values[0] + (values[1] - values[0]) * blend) / 255.0


_pixel_memo = {}        # (what, row colours, blend row colours, r|g<<8|b<<16) -> RGBA as an int
_row_ids = {}           # (what, row colours, blend row colours) -> small number, to keep keys cheap


def _map_pixels(pixels, palette, what, convert):
    """Run convert(red, green, blue) -> 4 bytes over every RGBA pixel (the 4th byte is ignored).

    Each different pixel value is worked out once and the picture is filled in with a table
    lookup. Results are also remembered between calls: a material pixel's look depends only
    on its own palette row and that row's blend row (see apply_palette), so after a colour
    edit only pixels of the changed row are worked out again. The palette editor redraws
    frames on every colour change, which this makes quick.
    """
    pixels = bytes(pixels)
    if len(pixels) % 4:
        raise ValueError("pixel data is not RGBA")
    if sys.byteorder != "little" or array("I").itemsize != 4:      # plain loop elsewhere
        return b"".join(convert(pixels[i], pixels[i + 1], pixels[i + 2])
                        for i in range(0, len(pixels), 4))
    if len(_pixel_memo) > 600000:
        _pixel_memo.clear()
        _row_ids.clear()
    rows = [tuple(tuple(cell) for cell in row) for row in palette]
    by_red = []
    for red in range(256):
        scaled = red * 4.0 / 255.0
        row = min(63, int((scaled - math.floor(scaled)) * 64.0))
        blend_row = max(0, min(63, int(shader_rgb(palette[row][3])[1])))
        by_red.append(_row_ids.setdefault((what, rows[row], rows[blend_row]), len(_row_ids)))
    words = memoryview(pixels).cast("I")
    table = {}
    for word in set(words):
        rgb = word & 0xFFFFFF
        key = (by_red[rgb & 255], rgb)
        hit = _pixel_memo.get(key)
        if hit is None:
            hit = _pixel_memo[key] = int.from_bytes(
                convert(rgb & 255, (rgb >> 8) & 255, rgb >> 16), "little")
        table[word] = hit
    return array("I", map(table.__getitem__, words)).tobytes()


def render_material(pixels, palette):
    """Type 3 material pixels -> RGBA as the game shows them (straight alpha).

    Colors come from apply_palette() (avatar-legends-tools). Alpha is corrected here:
    apply_palette() treats every non-empty pixel as solid, which turns the anti-aliased
    outer edge of the outline (stored on the transparent palette row 0) into a hard black
    staircase. Here alpha = fill opacity (from the palette) + line coverage (1 - G/255).
    """
    def convert(red, green, blue):
        if red == 0 and green == 255:
            return b"\0\0\0\0"
        color = apply_palette(bytes((red, green, blue, 255)), palette)
        edge = 1.0 - green / 255.0
        fill = _fill_alpha(red, blue, palette) * min(1.0, blue * 2.0 / 255.0)
        alpha = round(255.0 * max(0.0, min(1.0, fill + edge)))
        return bytes(color[:3]) + bytes((alpha,)) if alpha else b"\0\0\0\0"

    return _map_pixels(pixels, palette, "look", convert)


def row_map(pixels, palette):
    """Type 3 material pixels -> RGBA "which palette colour paints this pixel" map.

    For the palette editor's highlight. Per pixel:
      R = palette row (0 = none: empty, or only the black outline shows there)
      G = bit mask of the shade columns that colour it (1 = shadow, 2 = base, 4 = light)
      B = the row's blend row when it visibly mixes in, else 0
      A = how visible the pixel is (same alpha render_material() gives it)
    Same row / column selection as apply_palette(); a column or blend row only counts
    when it contributes at least 15% of the colour.
    """
    def convert(red, green, blue):
        if red == 0 and green == 255:
            return b"\0\0\0\0"
        edge = 1.0 - green / 255.0
        fill = _fill_alpha(red, blue, palette) * min(1.0, blue * 2.0 / 255.0)
        alpha = round(255.0 * max(0.0, min(1.0, fill + edge)))
        scaled = red * 4.0 / 255.0
        band = math.floor(scaled)
        row = min(63, int((scaled - band) * 64.0))
        if alpha and row and fill > 0.05 and edge < 0.9:
            shade = blue / 255.0
            shade = (shade - 0.5) / 0.6 if shade <= 0.8 else 0.5 + (shade - 0.8) / 0.3
            shade = max(0.0, min(1.0, shade))
            column = 0 if shade <= 0.5 else 1
            amount = shade * 2.0 - column
            mask = 0
            if 1.0 - amount >= 0.15:
                mask |= 1 << column
            if amount >= 0.15:
                mask |= 1 << (column + 1)
            blend = band / 7.0
            blend_row = max(0, min(63, int(shader_rgb(palette[row][3])[1])))
            if blend < 0.15 or blend_row == row:
                blend_row = 0
            return bytes((row, mask, blend_row, alpha))
        return bytes((0, 0, 0, alpha))

    return _map_pixels(pixels, palette, "rows", convert)


# ----------------------------------------------------------------- config

def load_config():
    # hide_export_rules: "Don't show this again" on the Export move dialog.
    # export_dirs: {project name: folder the user chose for exported PNGs}.
    # hide_install_notice: "Don't remind me again" on the Build & install confirmation.
    cfg = {"game_dir": DEFAULT_GAME_DIR, "character": "aang", "project": "my_mod",
           "hide_export_rules": False, "hide_install_notice": False, "export_dirs": {}}
    if CONFIG_PATH.is_file():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in saved.items() if k in cfg and type(v) is type(cfg[k])})
            cfg["export_dirs"] = {k: v for k, v in cfg["export_dirs"].items() if isinstance(v, str)}
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def check_char(char):
    if not isinstance(char, str) or not CHAR_RE.fullmatch(char):
        raise EditorError(f"invalid character name: {char!r}")
    return char


# ----------------------------------------------------------------- characters

FIGHTER_CATEGORIES = {"normals", "specials", "supers", "idle", "hitstun", "story_poses"}
_chars_cache = {}


def character_info(path):
    """{name, frames, main} if this pak file is a character pak, else None.

    A character pak has frames named sprites~<pak>~... in fighter categories and a
    sprites~<pak>~<pak>.sprbin. 'main' = has support characters (the playable roster).
    Only the pak directory is read, so this is fast; cached by file size + time.
    """
    path = Path(path)
    name = path.stem.lower()
    if not CHAR_RE.fullmatch(name):
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path).lower(), st.st_size, st.st_mtime_ns)
    if key not in _chars_cache:
        info = None
        try:
            entries = PakArchive(path).entries
            prefix = f"srcdata/munged/frames/sprites~{name}~"
            sprbin = f"srcdata/munged/scripts/sprites~{name}~{name}.sprbin"
            cats, count, has_sprbin = set(), 0, False
            for entry in entries:
                if entry.path.startswith(prefix):
                    count += 1
                    cats.add(entry.path[len(prefix):].split("~", 1)[0])
                elif entry.path == sprbin:
                    has_sprbin = True
            if has_sprbin and count and cats & FIGHTER_CATEGORIES:
                info = {"name": name, "frames": count, "main": "supportchar" in cats}
        except (PakError, OSError):
            info = None
        _chars_cache[key] = info
    return dict(_chars_cache[key]) if _chars_cache[key] else None


def _sorted_characters(items):
    return sorted(items, key=lambda c: (not c["main"], c["name"]))


def list_characters():
    """Character paks found in backup\\: [{name, frames, main}], main fighters first."""
    if not BACKUP_DIR.is_dir():
        return []
    found = (character_info(p) for p in BACKUP_DIR.glob("*.pak"))
    return _sorted_characters(c for c in found if c)


# ----------------------------------------------------------------- hashing

_hash_cache = {}
_hash_lock = threading.Lock()


def sha256_file(path):
    """SHA256 (upper-case hex). Cached by size + modified time."""
    path = Path(path)
    st = path.stat()
    key = (str(path).lower(), st.st_size, st.st_mtime_ns)
    with _hash_lock:
        if key in _hash_cache:
            return _hash_cache[key]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest().upper()
    with _hash_lock:
        _hash_cache[key] = digest
    return digest


# ----------------------------------------------------------------- backup

def backup_path(char):
    return BACKUP_DIR / f"{check_char(char)}.pak"


def _load_originals():
    if ORIGINALS_PATH.is_file():
        try:
            data = json.loads(ORIGINALS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, str)}
        except (OSError, ValueError):
            pass
    return {}


def expected_hash(char):
    """The hash the original pak must have, or None if we have never seen it."""
    return _load_originals().get(char)


def verify_backup(char):
    """Return the backup's SHA256, or raise if there is no trustworthy backup."""
    path = backup_path(char)
    if not path.is_file():
        raise EditorError(f"no backup found at {path}")
    digest = sha256_file(path)
    want = expected_hash(char)
    if want and digest != want:
        raise EditorError(
            f"backup\\{char}.pak does not match the known original "
            f"(got {digest[:12]}…, expected {want[:12]}…). Not using it.")
    if not want:
        originals = _load_originals()
        originals[char] = digest
        ORIGINALS_PATH.write_text(json.dumps(originals, indent=2), encoding="utf-8")
    return digest


def installed_hash(char):
    """Hash of the pak this editor last installed for the character, if any."""
    try:
        data = json.loads((DATA_DIR / "installed.json").read_text(encoding="utf-8"))
        return (data.get(char) or {}).get("hash")
    except (OSError, ValueError, AttributeError):
        return None


def _copy_new(src, dst):
    """Copy src to dst; fails instead of overwriting, and verifies the copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Path(src).open("rb") as fi, dst.open("xb") as fo:
        shutil.copyfileobj(fi, fo, 4 * 1024 * 1024)
    if sha256_file(dst) != sha256_file(src):
        dst.unlink(missing_ok=True)
        raise EditorError("backup copy does not match the source — disk problem?")


def _check_game_pak_is_stock(char, src, digest):
    if digest == installed_hash(char):
        raise EditorError(f"the game's {char}.pak is the mod this editor installed — "
                          "use Restore original first.")
    info = character_info(src)
    if info is None or info["name"] != char:
        raise EditorError(f"{src.name} does not look like a character pak")


def create_backup_if_missing(char, game_dir):
    """Copy the game's pak into backup\\ ONLY if no backup exists yet. Never overwrites.

    The pak is trusted to be stock: the page tells people to verify game files in Steam
    first. The hash is remembered so later checks can tell stock from modded.
    """
    dst = backup_path(char)
    if dst.exists():
        return False
    src = Path(game_dir) / f"{char}.pak"
    if not src.is_file():
        raise EditorError(f"{src} not found — check the game folder setting")
    digest = sha256_file(src)
    _check_game_pak_is_stock(char, src, digest)
    _copy_new(src, dst)
    originals = _load_originals()
    originals[char] = digest
    ORIGINALS_PATH.write_text(json.dumps(originals, indent=2), encoding="utf-8")
    return True


def game_pak_state(char, game_dir):
    """('original' | 'modded' | 'changed' | 'missing' | 'unknown', hash) for the game's pak.

    modded  = the pak this editor installed.
    changed = neither the backup nor our mod: the game updated, or another tool changed it.
    """
    path = Path(game_dir) / f"{char}.pak"
    if not path.is_file():
        return "missing", None
    try:
        digest = sha256_file(path)
    except OSError:
        return "unknown", None
    want = expected_hash(char)
    if not want:
        return "unknown", digest
    if digest == want:
        return "original", digest
    return ("modded" if digest == installed_hash(char) else "changed"), digest


# ----------------------------------------------------------------- scanning the game folder

def scan_game(game_dir):
    """Compare the game folder's character paks with the backups.

    Each entry: {name, frames, main, state} where state is
      new     - no backup yet (a character you have not set up, or one the game just added)
      ok      - game pak equals the backup
      modded  - game pak is the mod this editor installed
      update  - game pak differs from the backup and is not our mod (game update)
    Read-only. Hashes every character pak, so it takes a while on a cold cache.
    """
    folder = Path(game_dir)
    if not folder.is_dir():
        raise EditorError(f"game folder not found: {folder}")
    out = []
    for path in sorted(folder.glob("*.pak")):
        info = character_info(path)
        if not info:
            continue
        name = info["name"]
        backup = backup_path(name)
        if not backup.is_file():
            state = "new"
        else:
            digest = sha256_file(path)
            if digest == sha256_file(backup):
                state = "ok"
            elif digest == installed_hash(name):
                state = "modded"
            else:
                state = "update"
        out.append({**info, "state": state})
    return _sorted_characters(out)


def refresh_backup(char, game_dir):
    """After a game update: keep the old backup in backup\\previous\\ and back up the new pak.

    Nothing is deleted or overwritten. The new hash becomes the one future checks use.
    """
    old = backup_path(char)
    src = Path(game_dir) / f"{char}.pak"
    if not old.is_file():
        raise EditorError(f"no backup of {char} to refresh — use Back up instead")
    if not src.is_file():
        raise EditorError(f"{src} not found — check the game folder setting")
    digest = sha256_file(src)
    if digest == sha256_file(old):
        raise EditorError("the game's pak already matches the backup")
    _check_game_pak_is_stock(char, src, digest)
    keep = BACKUP_DIR / "previous" / f"{char}-{sha256_file(old)[:16].lower()}.pak"
    if keep.exists():
        raise EditorError(f"{keep.name} already exists in backup\\previous — not overwriting it")
    keep.parent.mkdir(parents=True, exist_ok=True)
    old.rename(keep)                                   # a move: the old backup is preserved
    try:
        _copy_new(src, old)
    except Exception:
        keep.rename(old)                               # put the old backup back
        raise
    originals = _load_originals()
    originals[char] = digest
    ORIGINALS_PATH.write_text(json.dumps(originals, indent=2), encoding="utf-8")
    with _ws_lock:
        _workspaces.pop(char, None)                    # re-read frames from the new pak
    return {"hash": digest, "kept": str(keep)}


# ----------------------------------------------------------------- cache

def cache_root(char, digest):
    return CACHE_DIR / f"{char}-{digest[:16].lower()}"


def cache_ready(char, digest):
    return (cache_root(char, digest) / ".complete").is_file()


_extract_lock = threading.Lock()


def ensure_cache(char):
    """Verify the backup and extract it into the cache (once). Returns (digest, root)."""
    digest = verify_backup(char)
    root = cache_root(char, digest)
    with _extract_lock:
        if (root / ".complete").is_file():
            return digest, root
        tmp = CACHE_DIR / f".tmp-{char}-{digest[:16].lower()}"
        if tmp.exists():
            shutil.rmtree(tmp)
        if root.exists():
            shutil.rmtree(root)            # half-finished earlier extraction (our own cache)
        tmp.mkdir(parents=True)
        try:
            archive = PakArchive(backup_path(char))
            archive.extract(tmp)           # writes tmp\<char>\...
        except (PakError, OSError) as err:
            shutil.rmtree(tmp, ignore_errors=True)
            raise EditorError(f"could not extract {char}.pak: {err}") from err
        tmp.rename(root)
        (root / ".complete").write_text(digest, encoding="ascii")
    return digest, root


def pak_src_dir(char, root):
    return root / char


def frames_dir(char, root):
    return pak_src_dir(char, root) / "srcdata" / "munged" / "frames"


def sprbin_path(char, root):
    return (pak_src_dir(char, root) / "srcdata" / "munged" / "scripts"
            / f"sprites~{char}~{char}.sprbin")


# ----------------------------------------------------------------- move index

def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


class Workspace:
    """Everything known about one extracted character pak. Built once, then read-only."""

    def __init__(self, char):
        self.char = check_char(char)
        self.digest, self.root = ensure_cache(char)
        self.frames_dir = frames_dir(char, self.root)
        if not self.frames_dir.is_dir():
            raise EditorError(f"{char}.pak has no frames folder — not a character pak?")
        self.png_dir = self.root / "png2"     # "png" held exports with hard outline edges
        self._info = {}
        self._lock = threading.Lock()
        self._cands = None
        self._materials = {}
        self._move_rows = {}
        self._cands_lock = threading.Lock()
        self._index()
        self._load_palettes()

    # -- filenames -> category / move / group / frame
    def _index(self):
        self.frames = {}                      # stem -> dict
        tree = {}
        for path in self.frames_dir.iterdir():
            if path.suffix.lower() != ".munged" or not path.is_file():
                continue
            stem = path.name[:-len(".munged")]
            parts = stem.split("~")
            if len(parts) < 5 or parts[0] != "sprites":
                category, move, group, frame = "(other)", "(other)", "", stem
            else:
                category, move = parts[2], parts[3]
                group = "~".join(parts[4:-1])
                frame = parts[-1]
            is_mask = frame.lower().endswith("_mask")
            self.frames[stem] = {
                "name": stem, "category": category, "move": move, "group": group,
                "frame": frame, "mask": is_mask,
                # 'frames...' groups are the character; everything else is effects/art
                "kind": "frames" if group.split("~")[-1].startswith("frames") or
                        "frames" in group.split("~") else "vfx",
            }
            tree.setdefault(category, {}).setdefault(move, {}).setdefault(group, []).append(stem)
        for moves in tree.values():
            for groups in moves.values():
                for names in groups.values():
                    names.sort(key=natural_key)
        self.tree = tree

    def tree_json(self):
        out = []
        for category in sorted(self.tree, key=natural_key):
            moves = []
            for move in sorted(self.tree[category], key=natural_key):
                groups = self.tree[category][move]
                moves.append({
                    "name": move,
                    "count": sum(len(v) for v in groups.values()),
                    "groups": [{"name": g, "count": len(groups[g])}
                               for g in sorted(groups, key=natural_key)],
                })
            out.append({"name": category, "moves": moves,
                        "count": sum(m["count"] for m in moves)})
        return out

    def move_frames(self, category, move):
        groups = self.tree.get(category, {}).get(move)
        if groups is None:
            raise EditorError(f"unknown move {category}/{move}")
        out = []
        for group in sorted(groups, key=natural_key):
            frames = []
            for stem in groups[group]:
                entry = dict(self.frames[stem])
                try:
                    entry.update(self.frame_info(stem))
                except (ValueError, OSError) as err:
                    entry["error"] = str(err)
                frames.append(entry)
            out.append({"name": group, "frames": frames})
        return out

    # -- palettes
    def _load_palettes(self):
        self.palette_records = []
        self.costume = {}
        self.default_palette = None
        path = sprbin_path(self.char, self.root)
        if not path.is_file() or path.stat().st_size > MAX_SPRBIN_BYTES:
            return
        data = path.read_bytes()
        records = [r for r in read_palette_records_data(data)
                   if is_selection_palette(r["source"])]
        self.palette_records = records
        costume = read_palette_records_data(data, costume_only=True)
        self.costume = {}                     # name -> record, in file order
        for record in costume:
            self.costume.setdefault(record["name"], record)
        try:
            self.default_palette = find_palette_record(costume, "_color1")
        except ValueError:
            self.default_palette = records[0] if records else None

    def palette_for(self, source):
        """Same choice munged_extract.py --auto-palette makes; falls back to color1."""
        record = None
        try:
            record = default_palette_record(source, self.palette_records)
        except ValueError:
            pass
        return record or self.default_palette

    # -- frames
    def frame_path(self, stem):
        if stem not in self.frames:           # whitelist: only names we listed ourselves
            raise EditorError(f"unknown frame: {stem}")
        return self.frames_dir / f"{stem}.munged"

    def _read(self, stem):
        path = self.frame_path(stem)
        if path.stat().st_size > MAX_MUNGED_BYTES:
            raise ValueError("frame file is unreasonably large")
        info, tiles, chunks = read_munged(path)
        if not (0 < info["crop_width"] <= MAX_DIM and 0 < info["crop_height"] <= MAX_DIM):
            raise ValueError("frame dimensions out of range")
        if info["raw_size"] > MAX_RAW_BYTES:
            raise ValueError("frame pixel data is unreasonably large")
        return info, tiles, chunks

    def frame_info(self, stem):
        with self._lock:
            hit = self._info.get(stem)
        if hit:
            return hit
        info = self._read(stem)[0]
        exponent = info["pixel_scale_code"] if info["pixel_scale_code"] in (0, 1, 2) else 0
        if info["image_type"] == 3:
            exponent -= 1
        out = {
            "type": info["image_type"],
            "w": info["crop_width"], "h": info["crop_height"],
            "x": info["crop_x"], "y": info["crop_y"],
            "canvas_w": info["canvas_width"], "canvas_h": info["canvas_height"],
            "scale": 2.0 ** exponent,
            "asset_key": info["asset_key"],
        }
        with self._lock:
            self._info[stem] = out
        return out

    def decode_rgba(self, stem):
        """-> (width, height, RGBA bytes, palette name or None). Cropped, like munged_extract."""
        info, tiles, chunks = self._read(stem)
        atlas = b"".join(decompress_lz4(data, size) for size, data in chunks)
        width, height, image = unpack_tiles(info, tiles, atlas)
        palette_name = None
        if info["image_type"] == 3:
            record = self.palette_for(info["source"])
            if record:
                image = render_material(image, record["palette"])
                palette_name = record["name"]
        return width, height, image, palette_name

    def material(self, stem):
        """-> (image type, width, height, raw pixels before any palette). Small cache."""
        with self._lock:
            hit = self._materials.get(stem)
        if hit:
            return hit
        info, tiles, chunks = self._read(stem)
        atlas = b"".join(decompress_lz4(data, size) for size, data in chunks)
        width, height, image = unpack_tiles(info, tiles, atlas)
        out = (info["image_type"], width, height, image)
        with self._lock:
            if len(self._materials) >= 48:
                self._materials.pop(next(iter(self._materials)))
            self._materials[stem] = out
        return out

    def png_file(self, stem, thumb=False):
        """Path of the cached PNG for a frame, decoding it first if needed."""
        from PIL import Image
        self.frame_path(stem)
        full = self.png_dir / "full" / f"{stem}.png"
        if not full.is_file():
            width, height, image, _ = self.decode_rgba(stem)
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp = full.with_name(f"{full.name}.{threading.get_ident()}.tmp")
            Image.frombytes("RGBA", (width, height), image).save(tmp, "PNG")
            _settle(tmp, full)
        if not thumb:
            return full
        small = self.png_dir / "thumb" / f"{stem}.png"
        if not small.is_file():
            small.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(full) as im:
                im = im.convert("RGBA")
                im.thumbnail((192, 192), Image.LANCZOS)
                tmp = small.with_name(f"{small.name}.{threading.get_ident()}.tmp")
                im.save(tmp, "PNG")
            _settle(tmp, small)
        return small


def _settle(tmp, target):
    """Move a finished cache file into place. Two requests can decode the same frame at
    once (e.g. thumbnail and full view); on Windows the second replace fails while the
    first file is being read, and then the file that is already there is just as good."""
    try:
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        if not target.is_file():
            raise


_workspaces = {}
_ws_lock = threading.Lock()


def workspace(char):
    with _ws_lock:
        ws = _workspaces.get(char)
        if ws is None:
            ws = _workspaces[char] = Workspace(char)
        return ws


# ----------------------------------------------------------------- pak building

def build_pak(char, out_path, overlay=None):
    """Rebuild <char>.pak from the cache, in the original pak's file order.

    overlay: {archive path, e.g. 'srcdata/munged/frames/x.munged': Path of replacement}
    With no overlay the result is byte-identical to the original.
    Returns (sha256, file count).
    """
    overlay = dict(overlay or {})
    digest, root = ensure_cache(char)
    src = pak_src_dir(char, root)
    out_path = Path(out_path).resolve()
    for forbidden in (BACKUP_DIR, APP_DIR.parent / "one_pak"):
        if forbidden.resolve() in out_path.parents:
            raise EditorError("refusing to write a build into a protected folder")
    try:
        entries = PakArchive(backup_path(char)).entries
    except PakError as err:
        raise EditorError(f"backup pak is damaged: {err}") from err
    known = {e.path for e in entries}
    unknown = sorted(set(overlay) - known)
    if unknown:
        raise EditorError(f"replacement is not a file of the original pak: {unknown[0]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    directory = []
    with out_path.open("wb") as out:
        out.write(b"PACK" + b"\0" * (PAK_DATA_START - 4))
        pos = PAK_DATA_START
        for entry in entries:
            if len(entry.path.encode("utf-8")) > PAK_NAME_LEN - 1:
                raise EditorError(f"name too long: {entry.path}")
            source = overlay.get(entry.path) or src.joinpath(*entry.path.split("/"))
            data = Path(source).read_bytes()
            if entry.path not in overlay and len(data) != entry.size:
                raise EditorError(f"cache file changed size: {entry.path} — delete "
                                  "skin-editor\\cache and run setup again")
            out.seek(pos)
            out.write(data)
            directory.append((entry.path, pos, len(data)))
            pos += len(data)
            pad = (-pos) % PAK_ALIGN
            out.write(b"\0" * pad)
            pos += pad
        # directory sits right after the LAST file's data, unaligned (as shipped)
        dir_off = directory[-1][1] + directory[-1][2] if directory else PAK_DATA_START
        out.seek(dir_off)
        out.truncate()
        for name, off, size in directory:
            out.write(name.encode("utf-8").ljust(PAK_NAME_LEN, b"\0") + struct.pack("<II", off, size))
        out.seek(4)
        out.write(struct.pack("<II", dir_off, len(directory) * 256))
    return sha256_file(out_path), len(directory)
