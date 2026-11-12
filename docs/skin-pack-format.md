# Skin pack format (version 1)

A skin pack is a plain **zip file** that carries one character's skin or recolor between players. It holds only
what the author made (their PNGs and palette changes), never the game's own files. Any tool can read or write it.

```
my-skin-aang.zip
  skin.json                     required, UTF-8 JSON
  frames/<frame name>.png       one PNG per edited frame (optional)
  preview.png                   small picture of the skin (optional, shown in the import window)
```

## skin.json

```json
{
  "format": 1,
  "kind": "avatar-legends-skin",
  "tool": "avatar-legends-skin-editor",
  "name": "Fire Nation Aang",
  "author": "someone",
  "description": "Free text.",
  "character": "aang",
  "created": "2026-11-10",
  "pak_sha256": "A0A97236...CD9E5",
  "frames": [
    { "name": "sprites~aang~specials~s.qcf+a~frames~atk_sp101", "sha256": "…" }
  ],
  "palettes": {
    "aang_color24": { "3": { "0": [20, 120, 40], "1": [40, 200, 70] } }
  }
}
```

- `format`: `1`. Readers must refuse formats they do not know. `kind` must be `avatar-legends-skin`.
- `character`: the character pak name (`aang`, `katara`, …). A pack holds one character.
- `frames[].name`: the frame's name as in the game's pak, i.e. the file name of
  `<character>/srcdata/munged/frames/<name>.munged` without the extension. The image is `frames/<name>.png`.
  Character frames and effects both work. The PNG must have exactly the size of the original frame (the size of
  the PNG the editor exports). Character frames are fitted to the importing player's palette on import; effect
  images are stored as drawn (straight, non-premultiplied alpha).
- `frames[].box` (optional, character frames only): `[x, y, width, height]` on the game's 3840×2160 screen canvas,
  for a PNG that is larger than the original frame (for example hair or a hat drawn above the head). The box must
  contain the original frame's rectangle, and the PNG must be exactly `width × height`. Without `box` the PNG has the
  original frame's size and position. The importer grows the frame to fit what was drawn.
- `palettes`: `{ palette slot: { row: { column: [r, g, b] } } }`. Rows are 0–63 and columns 0–2 (shadow, base,
  light). Only changed colors are listed. Column 3 (shader data) and alpha are never changed. Importers may map a
  slot to a different one, for example to put a recolor in a spare costume slot.
- `pak_sha256`: SHA256 of the original pak the author worked on. Informational: if it differs from the importer's
  pak, the game version differs and the importer should warn.
- `sha256`, `tool`, `created`, `name`, `author`, `description`, `preview.png` are informational. Text is
  user-supplied and must only be displayed as plain text.

## Reading a pack safely

Packs come from strangers. Importers should: limit the file, entry count and unpacked size; never use a name from
the manifest as a file path (look it up in the game's own frame list instead); check every PNG's size against the
game's frame; clamp colors and indexes; and show nothing from the pack as HTML. The editor in this repository does
all of this (`skinpack.py`).
