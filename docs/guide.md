# Frame Bender guide

Everything beyond the quick start in the [README](../README.md).

- [Recolour a costume](#recolour-a-costume)
- [Repaint frames](#repaint-frames)
- [How colours work](#how-colours-work)
- [Painting tips](#painting-tips)
- [Share or import a skin](#share-or-import-a-skin)
- [Possible errors](#possible-errors)
- [After a game update](#after-a-game-update)
- [Run from source](#run-from-source)
- [Build the Windows app](#build-the-windows-app)
- [How it works](#how-it-works)
- [What it does not do](#what-it-does-not-do)
- [Known limits](#known-limits)
- [Safety checks](#safety-checks)
- [Contributing](#contributing)

## Recolour a costume

About two minutes.

1. Pick a character and a move (Idle → stand is a good one).
2. The **Palette** panel on the right opens on the character's first costume slot. Choose another **costume
   slot** there: pick a spare one rather than the default costume, so the default look stays as it is.
3. Most rows have a label saying what they colour (Skin, Hair, Pants…). Point at a row and the stage darkens
   everything except the parts that row colours. Labels are a guide; the highlight on the stage is exact.
4. Click the **base** swatch. Drag in the colour square; the stage follows. Click **Set shadow and light from
   base** so the shading matches. **Done** keeps it, **Cancel** (or Esc) puts it back. Ctrl+Z undoes a change.
5. Close the game if it is running, then click **Build & install**.
6. Start the game, go to Training mode, and choose that costume colour at character select.
7. When you are finished, click **Restore original**.

## Repaint frames

1. Open the move and click **Export move**. Read the short rules it shows, and leave *Also export the palette as
   a PNG* ticked. A folder with the PNGs opens.
2. Paint over the PNGs. Keep the **file names** and the **canvas size**. Pick your colours from the
   `palette_….png` image: character frames can only use the costume's colours, and anything else is moved to
   the nearest allowed colour.
3. Drag the PNGs onto the Frame Bender window, as many as you like. You get a before / after for each.
4. **Build & install**, test in Training mode, then **Restore original**.

Need room for a hat or bigger hair? Click the small arrow next to Export move and change **Frame size** from
**Cropped** to **Full screen**: every PNG becomes the game's whole 3840×2160 screen with the frame at its real
spot, so you can draw anywhere around it. Tick **Use my current colours** there to get the frames drawn with the
costume slot you are editing in the Palette panel (your colour changes included) instead of the default costume.

## How colours work

Every part of a character uses a **palette row**: skin is one row, the robe another. Each row holds three colours
(shadow, base, light).

- When you **paint a PNG** in another program, take your colours from the exported `palette_….png`, which shows
  every colour the game can use. A colour that is not in it is swapped for the closest one that is.
- Once the PNG is **back in Frame Bender**, you can turn any row into any colour you like in the Palette panel.
  Everything painted with that row changes together.
- Want two colours in one place? Paint it with colours from two different rows.
- Want a new part (a shield, a hat) to have its own colour? Paint it with a row the character barely uses, then
  recolour that row. Point at a row first to see what else it colours.
- Parts you do not touch stay exactly as the game made them. Effects are full colour and have no palette limit,
  but an effect PNG must keep the exact size it was exported with: a resized or re-cropped one is rejected.

## Painting tips

- **You only need to paint what you change.** Every pixel you leave as exported stays exactly as the game has it,
  including the thin soft outline. Only repainted pixels are fitted to the palette.
- Colours from the rows the frame already uses come back exactly. A colour borrowed from another row that is
  almost the same as one of the frame's own is moved onto the frame's own row.
- The outline's outer edge is soft (semi-transparent black). Draw new outlines with an anti-aliased brush; a hard
  pencil edge stays hard in game.
- Each replaced frame takes a couple of seconds.
- Keep transparency transparent. Effect PNGs use plain (non-premultiplied) alpha.
- Masks (`…_mask`) are the cut-out shapes of some effects. Only moves that have them offer *Include effect
  masks*. They keep their data in the RGB channels even where alpha is 0, so they can look empty. Leave them
  alone unless you know what you are doing.

## Share or import a skin

- **Share**: click **Share**, give the skin a name, and send the zip it creates. If you edited several characters,
  tick the ones to share: each gets its own zip. Share the zip, never a `.pak`.
- **Import**: click **Import** or drop the zip on the window. You see what it would change before anything
  happens, and your own edits are kept unless you tick the box to replace them. Only import packs from people you
  trust.
- **Restore original** takes any installed skin out of the game, yours or one you imported. The skin stays in
  your project, so **Build & install** puts it back.

The zip format is described in [skin-pack-format.md](skin-pack-format.md).

## Possible errors

| You see | It means | What to do |
|---|---|---|
| "Add the tool scripts" screen | the four tool scripts are not there | click **Download the 4 scripts**, or copy them in by hand (the screen says where) |
| "The Frame Bender window could not open… opened in your browser instead" | Windows blocked files of the downloaded zip (common on Windows 10), or WebView2 is missing | it works in the browser as it is. To get the window back: delete the unzipped folder, right-click the zip → Properties → **Unblock**, unzip again. Or, in PowerShell inside the folder: `Get-ChildItem -Recurse \| Unblock-File` |
| Windows "protected your PC" / antivirus warning | the app is unsigned | *More info → Run anyway*, or build it yourself from this source |
| "A Python package Frame Bender needs is missing: flask" (or Pillow) | requirements not installed | `python -m pip install -r requirements.txt` |
| "The window component (pywebview) is not installed" | you ran `launcher.py` without it | `python -m pip install pywebview`, or use `run.bat` |
| "…needs the Microsoft Edge WebView2 Runtime" | WebView2 is missing (rare on current Windows) | install it from Microsoft (link in the message), or use `run.bat` |
| "could not start its local server on port 8765" | the app is already open, or another program uses the port | close the other copy and start again |
| "The game was not found in the usual Steam folders" / "There are no .pak files in that folder" | the game is somewhere unusual, or the wrong folder was picked | in Steam: right-click the game → Manage → Browse local files, open `data_packages`, and paste that path (or use **Browse…**) |
| Install or Restore says the file is in use | the game is running | close the game, then try again |
| Install or Restore refuses: the game file is not the backup or your mod | a game update changed the file | **Scan game folder… → Update backup** (in the "…" menu), then install again |
| "No verified backup" | that character was never backed up, or the backup changed | verify the game files in Steam, then **Scan game folder… → Back up** |
| A dropped PNG is rejected for its size | the canvas size changed | export again and keep the size, or use a *Full screen* export (character frames only; effects always keep their exported size) |
| A dropped PNG names another character | the file belongs to another character | switch to the character the message names |
| Many "far from the palette" pixels | you painted with colours the costume cannot show | pick colours from the exported palette image |
| The mod is gone after a game update | updates replace the game's files | **Scan game folder…**, update the backup, **Build & install** again |
| The colour did not change in game | you edited a different costume slot than you picked in game | check the slot name; test in Training mode |

`launcher.log` in the data folder (`%LOCALAPPDATA%\FrameBender` for the Windows app) records errors.

## After a game update

Updates replace the game's files, so your mod disappears. Open the "…" menu → **Scan game folder…**. A file
marked as changed by an update can be refreshed with **Update backup**: your old backup is kept in
`backup/previous/`, the new one becomes the reference, and your edits are rebuilt against it. Frames that no
longer exist are listed under **Edited files** and skipped. Then **Build & install** again.

## Run from source

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

## Build the Windows app

```bash
python -m pip install -r requirements-build.txt
```

```bash
python build_exe.py
```

This makes `dist\FrameBender\` (zip the folder to share it). Use Python 3.10.11 or newer; 3.10.0 has a bug that
breaks PyInstaller. If your system Python is older, [uv](https://docs.astral.sh/uv/) can make a separate build
environment without touching it:

```bash
uv venv --python 3.12 .venv-build
```

```bash
uv pip install --python .venv-build -r requirements-build.txt
```

```bash
.venv-build\Scripts\python build_exe.py
```

- Your data (backups, projects, cache, settings) is kept in `%LOCALAPPDATA%\FrameBender`. Start the exe with
  `--data <folder>` and/or `--backup <folder>` to use other folders. (Earlier builds were called
  `AvatarLegendsSkinEditor`; a data folder with that name is renamed to `FrameBender` on the first start.)
- The tool scripts are deliberately **not** inside the exe (they have no licence allowing redistribution), which
  is why the first start shows the setup screen.

## How it works

The game loads each character from `data_packages/<character>.pak`, and the only way to show a mod is to replace
that file with a rebuilt one. Character frames are palettized images, so a recolour edits the colour table in the
character's `.sprbin`, and replacing art means converting your PNG back into palette rows. Everything you edit
lives in `projects/<mod>/<character>/` as plain PNGs plus a small JSON file, so a mod can be rebuilt after a game
update. Nothing is written to the game folder except by **Build & install** and **Restore original**.

## What it does not do

- **No game files are included or shared**: not in this repository, the Windows app or skin packs. A skin pack
  holds only your PNGs and colour numbers.
- **No game code is touched.** It does not decompile, patch or inject into the game's program, and does not
  bypass copy protection. It only rewrites images and colour tables inside the character files.
- **No paid content is unlocked.** DLC costume slots are labelled; editing one does not give you that costume.
- **Not a cheat.** Looks only: hitboxes, damage, frame counts and timing stay the same.

## Known limits

- Tested in game: palette recolouring on most characters, and frame replacement (soft outline, taller frames) on
  Aang. If something looks wrong on a character, please open an issue and say which one.
- Replacing effects is checked against the shipped files but has not been tried in game yet.
- HUD and menu images, music and voices are not supported. Frame counts and timing cannot be changed.
- Undo covers colour changes, not frame replacements (use Revert for those).
- One project (`my_mod`); no project switcher yet.
- Windows only for now.

## Safety checks

You don't have to take the app on trust:

- **Test rebuild** (in the "…" menu) rebuilds a character with no edits and checks the result is byte-for-byte
  the original file, and that writing the palettes back unchanged is identical.
- Every file is backed up before anything changes, and **Restore original** checks the result against the backup
  (SHA256).
- It never downloads or installs anything silently, never writes to the game folder outside the two labelled
  buttons, and never changes a backup. The app only listens on `127.0.0.1`: no account, no telemetry.
- The code is here to read. If you find a mistake, please open an issue.

## Contributing

Issues and pull requests are welcome: results with other characters, game-update handling, effects in game.
Please do not attach game files or modded paks to issues; screenshots and PNGs of your own art are fine.
