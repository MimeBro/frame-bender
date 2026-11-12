#!/usr/bin/env python3
"""Snap a repainted PNG to the character's real palette and encode a type-3 frame.

You repaint over the colored PNG that munged_extract.py produced. This script
replaces every opaque pixel with the nearest color the character's COSTUME
palette can actually show (row x 3 shade columns), writes the matching row
numbers into the frame, and keeps the original frame's placement, flags and
asset key. Recolor the palette later and every frame follows.

usage:
  snap_frame.py <char.sprbin> <painted.png> <original.munged> <out.munged>
                [--palette-name _color1] [--ref-dir DIR] [--min-count 30]
                [--preview preview.png]
  snap_frame.py <char.sprbin> --list
"""
import argparse
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from munged_extract import (apply_palette, decompress_lz4, find_palette_record,
                            read_munged, read_palette_records_data, unpack_tiles)
from type3_encode import synth_type3

# material blue values that select palette column 0 / 1 / 2 (see apply_palette)
SHADES = (128, 204, 242)
# material green = edge/linework: 255 = plain color, lower = darker, 0 = black
EDGES = (255, 224, 192, 160, 128, 96, 64, 48, 32, 16, 0)
TRANSPARENT = b"\x00\xff\x00\x00"


def load_palette(sprbin, name):
    data = Path(sprbin).read_bytes()
    records = read_palette_records_data(data, costume_only=True)
    if not records:
        sys.exit("no costume palettes found in that .sprbin")
    names = [r["name"] for r in records]
    try:
        rec = find_palette_record(records, name)
    except ValueError as err:
        sys.exit(f"{err}\navailable: {', '.join(names)}")
    return rec["palette"], rec["name"], names


def rows_used(paths, min_count):
    """Palette rows the given original type-3 frames really use."""
    counts = Counter()
    for p in paths:
        info, tiles, chunks = read_munged(Path(p))
        if info["image_type"] != 3:
            continue
        atlas = b"".join(decompress_lz4(d, s) for s, d in chunks)
        _, _, img = unpack_tiles(info, tiles, atlas)
        for (r, g), n in Counter(zip(img[0::4], img[1::4])).items():
            if r == 0 and g == 255:
                continue
            scaled = r * 4.0 / 255.0
            counts[min(63, int((scaled - math.floor(scaled)) * 64.0))] += n
    return sorted(row for row, n in counts.items() if n >= min_count and row != 0)


def build_candidates(palette, rows):
    """[(rgb, row, green, blue)] -- exactly what the game would show for each choice."""
    out, seen = [], set()
    for row in rows:
        for blue in SHADES:
            for green in EDGES:
                px = apply_palette(bytes((row, green, blue, 255)), palette)
                rgb = (px[0], px[1], px[2])
                if px[3] == 255 and rgb not in seen:
                    seen.add(rgb)
                    out.append((rgb, row, green, blue))
    return out


def snap(rgba, cands, alpha_cut=128):
    """RGBA bytes -> (material bytes, mean error, count of poor matches)."""
    material = bytearray(len(rgba))
    cache, total, n_opaque, poor = {}, 0.0, 0, Counter()
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i:i + 4]
        if a < alpha_cut:
            material[i:i + 4] = TRANSPARENT
            continue
        hit = cache.get((r, g, b))
        if hit is None:
            best = min(cands, key=lambda c: (c[0][0] - r) ** 2 + (c[0][1] - g) ** 2 + (c[0][2] - b) ** 2)
            dist = math.sqrt((best[0][0] - r) ** 2 + (best[0][1] - g) ** 2 + (best[0][2] - b) ** 2)
            hit = cache[(r, g, b)] = (best[1], best[2], best[3], dist)
        row, green, blue, dist = hit
        material[i:i + 4] = bytes((row, green, blue, 255))
        total += dist
        n_opaque += 1
        if dist > 40:
            poor[(r, g, b)] += 1
    return bytes(material), (total / n_opaque if n_opaque else 0.0), poor


def encode(material, w, h, orig_path):
    orig = Path(orig_path)
    info = read_munged(orig)[0]
    blob = bytearray(synth_type3(material, w, h, source_name=info["source"],
                                 template_prefix=orig.read_bytes()))
    name_len = struct.unpack_from("<I", blob, 27)[0]
    off = 27 + 4 + name_len
    struct.pack_into("<6I", blob, off, info["crop_x"], info["crop_y"], w, h,
                     info["canvas_width"], info["canvas_height"])
    return bytes(blob), info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sprbin")
    ap.add_argument("painted", nargs="?")
    ap.add_argument("original", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--palette-name", default="_color1")
    ap.add_argument("--ref-dir", help="folder of original .munged frames; their rows become allowed")
    ap.add_argument("--min-count", type=int, default=30)
    ap.add_argument("--preview")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    palette, pname, names = load_palette(a.sprbin, a.palette_name)
    if a.list:
        print("costume palettes:", ", ".join(names))
        return
    if not (a.painted and a.original and a.out):
        ap.error("need painted.png, original.munged and out.munged")

    from PIL import Image
    im = Image.open(a.painted).convert("RGBA")
    w, h = im.size
    info0 = read_munged(Path(a.original))[0]
    if (w, h) != (info0["crop_width"], info0["crop_height"]):
        sys.exit(f"painted size {w}x{h} != original crop {info0['crop_width']}x{info0['crop_height']}; "
                 "keep the canvas size of the extracted PNG")

    refs = [a.original]
    if a.ref_dir:
        refs += [str(p) for p in Path(a.ref_dir).glob("*~frames~*.munged")]
    rows = rows_used(refs, a.min_count)
    cands = build_candidates(palette, rows)
    print(f"palette: {pname} | rows allowed: {len(rows)} {rows}")

    material, mean_err, poor = snap(im.tobytes(), cands)
    blob, info = encode(material, w, h, a.original)
    Path(a.out).write_bytes(blob)
    new = read_munged(Path(a.out))[0]
    ok = all(info[k] == new[k] for k in ("crop_x", "crop_y", "canvas_width", "canvas_height", "asset_key"))
    print(f"mean color error: {mean_err:.1f} | placement/key kept: {ok}")
    if poor:
        worst = ", ".join(f"{c}x{n}" for c, n in poor.most_common(5))
        print(f"colors far from the palette (>40): {sum(poor.values())} px, e.g. {worst}")
    if a.preview:
        shown = apply_palette(material, palette)
        Image.frombytes("RGBA", (w, h), bytes(shown)).save(a.preview)
        print("preview ->", a.preview)


if __name__ == "__main__":
    main()
