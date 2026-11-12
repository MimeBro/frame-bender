"""Encoder for type 6 (true color, RGBA) frames: effects (vfx), masks and UI images.

Character frames are palettized (type 3, see snap_frame.py). Effects store plain RGBA in
32x32 tiles, each padded to a 34x34 cell with a 1 px clamped border, packed into an atlas
and LZ4-compressed in 591872-byte chunks. Tiles that are entirely zero are not stored.

This writes the same layout as the shipped files (verified for every type 6 frame of
aang.pak: identical header, tile occupancy and chunk sizes; pixels decode back identical).
The LZ4 data itself is stored as literals (valid, just not compressed).

munged_encode.py (avatar-legends-tools) keeps the ORIGINAL occupancy, which loses any new
pixels painted in a previously empty tile; here occupancy follows the new pixels.
"""
import struct
from pathlib import Path

import core                                  # puts avatar-legends-tools on sys.path
from munged_encode import build_atlas, lz4_literal
from munged_extract import read_munged

CHUNK_BYTES = 591872      # raw bytes per LZ4 chunk in shipped files
MAX_COLUMNS = 30          # atlas width limit in cells (1020 px)


def encode_type6(orig_path, rgba):
    """RGBA bytes (crop size of the original frame) -> munged file bytes for that frame."""
    orig_path = Path(orig_path)
    data = orig_path.read_bytes()
    info, _, _ = read_munged(orig_path)
    if info["image_type"] != 6:
        raise ValueError("not a type 6 frame")
    w, h = info["crop_width"], info["crop_height"]
    if len(rgba) != w * h * 4:
        raise ValueError("pixel data does not match the frame size")
    tc, tr = info["tile_columns"], info["tile_rows"]
    count = tc * tr

    occupancy = bytearray((count + 7) // 8)
    stored = 0
    for tile in range(count):
        tx, ty = tile % tc, tile // tc
        cw, ch = min(32, w - tx * 32), min(32, h - ty * 32)
        for y in range(ch):
            start = ((ty * 32 + y) * w + tx * 32) * 4
            if any(rgba[start:start + cw * 4]):
                occupancy[tile >> 3] |= 1 << (tile & 7)
                stored += 1
                break

    if stored:
        cols = min(MAX_COLUMNS, stored)
        rows = -(-stored // cols)
        aw, ah = cols * 34, rows * 34
    else:                                   # fully empty image: one blank 1x34 strip, as shipped
        cols, rows, aw, ah = 0, 1, 1, 34
    new = dict(info, atlas_width=aw, atlas_height=ah, atlas_columns=cols, atlas_rows=rows,
               stored_tiles=stored)
    atlas = build_atlas(new, bytes(occupancy), bytes(rgba)) if stored else bytes(aw * ah * 4)
    if len(atlas) != aw * ah * 4:
        raise ValueError("internal error: atlas size mismatch")

    name_end = 31 + struct.unpack_from("<I", data, 27)[0]
    out = bytearray(data[:name_end + 60])
    for index, key in ((6, "atlas_width"), (7, "atlas_height"), (11, "atlas_columns"),
                       (12, "atlas_rows"), (13, "stored_tiles")):
        struct.pack_into("<I", out, name_end + index * 4, new[key])
    out += occupancy
    pieces = [atlas[i:i + CHUNK_BYTES] for i in range(0, len(atlas), CHUNK_BYTES)] or [b""]
    out += struct.pack("<I", len(pieces))
    for piece in pieces:
        stream = lz4_literal(piece)
        out += struct.pack("<3I", len(stream) + 8, len(piece), len(stream))
        out += stream
    return bytes(out)
