# Frame Bender

*A skin and frame editor for Avatar Legends: The Fighting Game (PC / Steam).*

Recolour costumes with a live preview, repaint animation frames in your own art program, and put the result in your
game with one click. One more click puts the original back.

> **Unofficial fan project.** Not affiliated with or endorsed by the game's developers or publisher, or by
> Nickelodeon / Paramount. This repository contains **no game files**. You need your own copy of the game.
> Tested in online matches without issues. As with any mod, use it at your own risk (see [Risks](#risks)).

<!-- Add screenshots or the demo video here: the palette editor, a recoloured character in game. -->

## What it is, and what it is not

**It is** an editor for the *pictures and colours* of characters in a copy of the game you already own:

- It reads the character files in your own game folder, shows the animations, and lets you change colours and
  frames.
- It keeps an untouched backup of every file before changing anything, and can restore it (checked by SHA256).
- Everything runs on your PC. The app only listens on `127.0.0.1`, has no account, no telemetry, and downloads
  nothing unless you click a button that says so.

**It is not**, and does not do, any of the following:

- **No game files are included or shared.** Not in this repository, not in the Windows app, not in skin packs.
  A recolour-only skin pack is just a list of colour numbers. A pack with repainted frames contains your edited
  PNGs only, never the game's `.pak` files.
- **No game code is touched.** The app does not decompile, patch or inject into the game's program, and it does not
  bypass any copy protection or DRM. It only rewrites data files (images and colour tables) inside the character
  packages.
- **It does not unlock paid content.** DLC costume slots are labelled as such; editing a slot does not give you a
  costume you do not own.
- **It is not a cheat.** It changes how a character looks in your own copy of the game. It does not change
  hitboxes, damage, frame counts or timing.
- **It is not the format research.** How the game's files are laid out was worked out and published by
  [avatar-legends-tools](https://github.com/calebrisc/avatar-legends-tools) by calebrisc. Frame Bender is an
  editor built on top of those tools (see [Credits](#credits)).

## Features

- **Every character**, supports included (Appa, Momo, Gyatso, …), with a searchable character list.
- **Stage view**: the animation plays on the game's own stages, or on a checkerboard. Zoom 10–200%, drag to pan.
  Backgrounds only: in-game lighting is not shown.
- **Timeline** of frame thumbnails. Select like in Windows (Ctrl+click, Shift+click, Ctrl+A), revert one frame,
  a selection or a whole move. Space plays, ←/→ step.
- **Palette editor**
  - Pick a costume slot and click a swatch: a colour picker opens inside the panel (it never covers the swatches
    or the stage) and the stage previews while you drag.
  - Point at a row to see which parts of the character it colours.
  - **Set shadow and light from base**: change one colour and get matching shading, in the costume's own style.
  - Eyedropper, hex and RGB fields, **undo / redo** (Ctrl+Z, Ctrl+Y).
- **Export, paint, drop back**: export a move as PNGs, paint in Photoshop (or anything), drop the files on the
  window. Character frames are fitted to the colours the game can show, with a before / after preview.
  Optional extra canvas space for hair, hats and wigs. Effects (fire, wind, …) keep full colour.
- **Skin packs**: share a small zip with only your edits; import one with a preview of what it will change.
  See [docs/skin-pack-format.md](docs/skin-pack-format.md).
- **Build & install / Restore original**: one button each. The status card at the top shows whether the game has the original or your mod.
- **Scan game**: finds new characters and notices when a game update changed a file.

## Quick start

### A. Windows app (no Python needed)

1. Download the `AvatarLegendsSkinEditor` zip from this repository's **Releases** page (if there is no release
   yet, build it yourself: see [Build the Windows app](#build-the-windows-app)). Before unzipping, right-click the
   zip → **Properties** → tick **Unblock** → OK. That tells Windows you trust the download, so it will not warn
   about every file inside. Then unzip it anywhere.
2. Double-click `AvatarLegendsSkinEditor.exe`. Windows may warn that the app is unsigned: choose
   *More info → Run anyway* if you trust this source, or build it yourself.
3. The first start shows a **setup screen**. The app needs four small scripts from avatar-legends-tools, which it
   is not allowed to include. Click **Download the 4 files** (it shows the exact addresses and verifies each file),
   or copy them in by hand as the screen explains.
4. Continue with [First run](#first-run).

### B. From source

Needs Windows, Python 3.10.11 or newer (3.12 recommended) and `git`.

```bash
git clone --recursive https://github.com/MimeBro/frame-bender.git
```

```bash
cd frame-bender
```

```bash
python -m pip install -r requirements.txt
```

```bash
python app.py
```

Then open <http://127.0.0.1:8765>, or double-click `run.bat`. `python launcher.py` opens the editor in its own
window instead (needs `pip install pywebview`).

Use `git clone --recursive`: the tools come as a git submodule, and GitHub's "Download ZIP" does not include
them. If you cloned without it, run `git submodule update --init`.

### First run

1. In Steam, **verify the game files** (right-click the game → Properties → Installed Files → Verify). Your backup
   is a copy of whatever is in the game folder now, so it must be the stock file.
2. Check the folder shown is your game's `data_packages` folder (the default Steam path is pre-filled) and click
   **Save and scan**.
3. Click **Back up** next to the characters you want to edit. Backups are never modified afterwards.
4. Pick a character. The first time, unpacking takes up to a minute.

## Tutorials

### 1. Recolour a costume (about two minutes)

1. Pick a character and a move (Idle → stand is a good one).
2. In the **Palette** panel on the right, choose a **costume slot**. Pick a spare one rather than the default
   costume, so the default look stays as it is.
3. Point at the rows: the stage darkens everything except the parts that row colours. Find the row you want.
4. Click the **base** swatch. Drag in the colour square; the stage follows. Click **Set shadow and light from
   base** so the shading matches. **Done** keeps it, **Cancel** (or Esc) puts it back. Ctrl+Z undoes a change.
5. Close the game if it is running, then click **Build & install**.
6. Start the game, go to Training mode, and choose that costume colour at character select.
7. When you are finished, click **Restore original**.

### 2. Repaint frames

1. Open the move and click **Export move**. Read the short rules it shows, and leave *Also export the palette as
   a PNG* ticked. A folder with the PNGs opens.
2. Paint over the PNGs. Keep the **file names** and the **canvas size**. Pick your colours from the
   `palette_….png` image: character frames can only use the costume's colours, and anything else is moved to
   the nearest allowed colour.
3. Drag the PNGs onto the Frame Bender window, as many as you like. You get a before / after for each.
4. **Build & install**, test in Training mode, then **Restore original**.

Need room for a hat or bigger hair? Click the small arrow next to Export move and choose **extra space** before
exporting.

### How colours work

Every part of a character uses a **palette row**: skin is one row, the robe another. Each row holds three colours
(shadow, base, light).

- When you **paint a PNG** in another program, take your colours from the exported `palette_….png`, which shows
  every colour the game can use. A colour that is not in it is swapped for the closest one that is.
- Once the PNG is **back in Frame Bender**, you can turn any row into any colour you like in the Palette panel.
  Everything painted with that row changes together.
- Want two colours in one place? Paint it with colours from two different rows.
- Parts you do not touch stay exactly as the game made them. Effects are full colour and have no palette limit.

### 3. Share or import a skin

- **Share**: click **Share**, give the skin a name, and send the zip it creates. Share the zip, never a `.pak`.
- **Import**: click **Import** or drop the zip on the window. You see what it would change before anything
  happens, and your own edits are kept unless you tick the box to replace them. Only import packs from people you
  trust.

## Possible errors

| You see | It means | What to do |
|---|---|---|
| Setup screen "Missing: munged_extract.py, …" | the four tool scripts are not there | click **Download the 4 files**, or copy them in by hand (the screen says where) |
| Windows "protected your PC" / antivirus warning | the app is unsigned | *More info → Run anyway*, or build it yourself from this source |
| "A Python package the editor needs is missing: flask" (or Pillow) | requirements not installed | `python -m pip install -r requirements.txt` |
| "The window component (pywebview) is not installed" | you ran `launcher.py` without it | `python -m pip install pywebview`, or use `run.bat` |
| "…needs the Microsoft Edge WebView2 Runtime" | WebView2 is missing (rare on current Windows) | install it from Microsoft (link in the message), or use `run.bat` |
| "could not start its local server on port 8765" | the app is already open, or another program uses the port | close the other copy and start again |
| "game folder not found" / "no .pak files in that folder" | wrong folder | paste the game's `data_packages` folder |
| Install or Restore says the file is in use | the game is running | close the game, then try again |
| Install or Restore refuses: the game file is not the backup or your mod | a game update changed the file | **Scan game folder… → Update backup** (in the "…" menu), then install again |
| "No verified backup" | that character was never backed up, or the backup changed | verify the game files in Steam, then **Scan game folder… → Back up** |
| A dropped PNG is rejected for its size | the canvas size changed | export again and keep the size, or use an *extra space* export |
| A dropped PNG names another character | the file belongs to another character | switch to the character the message names |
| Many "far from the palette" pixels | you painted with colours the costume cannot show | pick colours from the exported palette image |
| The mod is gone after a game update | updates replace the game's files | **Scan game folder…**, update the backup, **Build & install** again |
| The colour did not change in game | you edited a different costume slot than you picked in game | check the slot name; test in Training mode |

`launcher.log` in the data folder (`%LOCALAPPDATA%\AvatarLegendsSkinEditor` for the Windows app) records errors.

## Painting tips

- **You only need to paint what you change.** Every pixel you leave as exported stays exactly as the game has it,
  including the thin soft outline. Only repainted pixels are fitted to the palette.
- Colours from the rows the frame already uses come back exactly. A colour borrowed from another row that is
  almost the same as one of the frame's own is moved onto the frame's own row.
- The outline's outer edge is soft (semi-transparent black). Draw new outlines with an anti-aliased brush; a hard
  pencil edge stays hard in game.
- The first replacement for a character takes up to a minute; after that each frame takes a couple of seconds.
- Keep transparency transparent. Effect PNGs use plain (non-premultiplied) alpha.
- Masks (`…_mask`) keep their data in the RGB channels even where alpha is 0, so they can look empty. Leave them
  alone unless you know what you are doing.

## After a game update

Open the "…" menu → **Scan game folder…**. A file marked as changed by an update can be refreshed with **Update
backup**: your old backup is kept in `backup/previous/`, the new one becomes the reference, and your edits are
rebuilt against it. Frames that no longer exist are listed under **Edited files** and skipped.

## Build the Windows app

```bash
python -m pip install -r requirements-build.txt
```

```bash
python build_exe.py
```

This makes `dist\AvatarLegendsSkinEditor\` (zip the folder to share it). Use Python 3.10.11 or newer; 3.10.0 has
a bug that breaks PyInstaller. If your system Python is older, [uv](https://docs.astral.sh/uv/) can make a
separate build environment without touching it:

```bash
uv venv --python 3.12 .venv-build
```

```bash
uv pip install --python .venv-build -r requirements-build.txt
```

```bash
.venv-build\Scripts\python build_exe.py
```

- Your data (backups, projects, cache, settings) is kept in `%LOCALAPPDATA%\AvatarLegendsSkinEditor`. Start the
  exe with `--data <folder>` and/or `--backup <folder>` to use other folders.
- The tool scripts are deliberately **not** inside the exe (they have no licence allowing redistribution), which
  is why the first start shows the setup screen.

## How it works (short version)

The game loads each character from `data_packages/<character>.pak`, and the only way to show a mod is to replace
that file with a rebuilt one. Character frames are palettized images, so a recolour edits the colour table in the
character's `.sprbin`, and replacing art means converting your PNG back into palette rows. Everything you edit
lives in `projects/<mod>/<character>/` as plain PNGs plus a small JSON file, so a mod can be rebuilt after a game
update. Nothing is written to the game folder except by **Build & install** and **Restore original**.

## Known limits

- Tested in game: palette recolouring on most characters, and frame replacement (soft outline, taller frames) on
  Aang. If something looks wrong on a character, please open an issue and say which one.
- Replacing effects is checked against the shipped files but has not been tried in game yet.
- HUD and menu images, music and voices are not supported. Frame counts and timing cannot be changed.
- Undo covers colour changes, not frame replacements (use Revert for those).
- One project (`my_mod`); no project switcher yet.

## Risks

- **Modified files have been tested in online matches without issues.** As with any mod, the game's developers
  could change their rules at any time, so use is at the user's own risk. Use **Restore original** whenever you want the stock files back, and read the game's terms yourself.
  A short reminder is shown before **Build & install**; you can switch it off there.
- Game updates overwrite mods. Keep your `projects/` folder; it is all you need to rebuild.
- If the game is running, Windows may lock the file. Close the game before installing or restoring.
- Use at your own risk. No warranty (see the licence).

## Credits

- **[avatar-legends-tools](https://github.com/calebrisc/avatar-legends-tools) by calebrisc.** The research this
  project stands on: the `.pak` format, the image formats, the palette tables, and the tools that read and write
  them. Frame Bender uses four of those scripts unmodified (a git submodule, or a checksum-verified download that
  you start yourself); they are never copied into this repository.
- The editor itself (move browser, stage view, palette editor, fitting painted frames to the palette, backups,
  install / restore, skin packs) is by the maintainer of this repository.

## About AI

This project was developed with the assistance of an AI coding assistant (Claude). To make that trustworthy rather
than a leap of faith:

- The app proves its own basics: **Test rebuild** (in the "…" menu) rebuilds a character with no edits and
  checks the result is byte-for-byte the original file, and that writing the palettes back unchanged is identical.
- Recolours, replaced frames, the outline and taller frames were checked in the actual game.
- It never downloads or installs anything silently, never writes to the game folder outside the two labelled
  buttons, and never changes a backup.
- The code is here to read. If you find a mistake, please open an issue.

## Contributing

Issues and pull requests are welcome: results with other characters, game-update handling, effects in game.
Please do not attach game files or modded paks to issues; screenshots and PNGs of your own art are fine.

## License

MIT for the code in this repository (see `LICENSE`). `avatar-legends-tools` is a separate project with its own
terms. Game assets belong to their owners and are not part of this repository.
