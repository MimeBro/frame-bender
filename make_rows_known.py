"""Maintainer tool: work out which palette rows each character's frames use, once, so the
app does not have to (it takes a minute or more per character on a user's PC).

    python make_rows_known.py            every character pak in the backup folder
    python make_rows_known.py aang zuko  only these

Writes rows_known.json: {character: {"sha256": <hash of the pak>, "rows": [...]}}. The app uses
an entry only when the user's pak has the same hash; after a game update it works the rows out
itself (in the background) until this file is refreshed. The file holds row numbers only, no
game data. Reads the paks in memory; nothing is unpacked or written anywhere else.
"""
import json
import math
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "rows_known.json"
MIN_COUNT = 30                      # same threshold as mods.allowed_rows


class _Blob:
    """Just enough of a Path for munged_extract.read_munged."""

    def __init__(self, data):
        self.data = data

    def read_bytes(self):
        return self.data


def rows_of(char):
    import core
    from munged_extract import decompress_lz4, read_munged, unpack_tiles
    from pak_extract import PakArchive
    path = core.backup_path(char)
    digest = core.sha256_file(path)
    prefix = "srcdata/munged/frames/"
    frames = []                                        # (entry, is character frame)
    for entry in PakArchive(path).entries:
        if not entry.path.startswith(prefix) or not entry.path.lower().endswith(".munged"):
            continue
        stem = entry.path[len(prefix):-len(".munged")]
        parts = stem.split("~")
        if len(parts) < 5 or parts[0] != "sprites":
            group, frame = "", stem
        else:
            group, frame = "~".join(parts[4:-1]), parts[-1]
        if frame.lower().endswith("_mask"):
            continue
        bits = group.split("~")
        frames.append((entry, bits[-1].startswith("frames") or "frames" in bits))
    chosen = [e for e, is_char in frames if is_char] or [e for e, _ in frames]
    counts = Counter()
    with path.open("rb") as f:
        for entry in chosen:                           # the same maths as snap_frame.rows_used
            f.seek(entry.offset)
            info, tiles, chunks = read_munged(_Blob(f.read(entry.size)))
            if info["image_type"] != 3:
                continue
            atlas = b"".join(decompress_lz4(d, s) for s, d in chunks)
            _, _, img = unpack_tiles(info, tiles, atlas)
            for (r, g), n in Counter(zip(img[0::4], img[1::4])).items():
                if r == 0 and g == 255:
                    continue
                scaled = r * 4.0 / 255.0
                counts[min(63, int((scaled - math.floor(scaled)) * 64.0))] += n
    rows = sorted(row for row, n in counts.items() if n >= MIN_COUNT and row != 0)
    return char, digest, rows, len(chosen)


def main(names):
    import core
    names = names or [c["name"] for c in core.list_characters()]
    known = json.loads(OUT.read_text(encoding="ascii")) if OUT.is_file() else {}
    with Pool(6) as pool:
        for char, digest, rows, count in pool.imap_unordered(rows_of, names):
            known[char] = {"sha256": digest, "rows": rows}
            OUT.write_text(json.dumps(dict(sorted(known.items())), indent=1), encoding="ascii")
            print(f"{char}: {count} frames, rows {rows}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
