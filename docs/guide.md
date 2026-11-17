# Frame Bender guide

Everything beyond the quick start in the [README](../README.md).

- [Recolour a costume](#recolour-a-costume)
- [How colours work](#how-colours-work)
- [Repaint frames](#repaint-frames)
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

## How colours work

Read this once before you repaint anything. It is the part that trips most people up.

<!-- IMAGE: the Palette panel next to the character, pointing at one row so only that part stays lit on the stage. -->

**Each character has a palette: a numbered list of rows.** Each row is one part of the character: skin, hair,
shirt, boots and so on. A row has three shades of its colour: shadow, base and light.

**A pixel belongs to a row, not to a colour.** The game does not store "this pixel is red". It stores "this
pixel is the shirt, base shade". The colour comes from the palette. That is why changing one row recolours that
part in every frame of every move at once.

So there are two different jobs, done in two different places:

| You want to change… | Do it in | How |
|---|---|---|
| **What colour** a part is | Frame Bender's Palette panel | Pick a row, change its colours. Any colour you like. |
| **The shape** of something (a new hat, a longer sword, a scar) | Your art program | Paint it with a row's shades, then drop the PNG back. |

<!-- IMAGE: a row being recoloured in the Palette panel, before / after on the stage. -->

### Painting with rows

When you export a move, Frame Bender also saves a `palette_….png` next to the frames. It shows every row with
its three shades.

<!-- IMAGE: the exported palette PNG, with one row's three shades marked. -->

- **Pick your colours from that image** with the eyedropper. When you drop the PNG back, every pixel you painted
  is matched to the closest shade in the palette, and that decides which row (which part) it belongs to.
- **The palette image shows the default costume's colours.** That is on purpose: you are choosing *which row*,
  not the final colour. Paint a hat with the hair row's shades, and the hat will always match the hair, in every
  costume.
- **To give a new part its own colour**, paint it with a row the character barely uses, then recolour that row
  in Frame Bender. Point at the row first: the stage shows what else it colours.
- **Two colours in one area** (a shield with a blue face and a silver rim) need two rows.
- Colours that are not in the palette are moved to the closest one that is. The result after dropping shows how
  many pixels were "far from the palette"; a high number means some colours did not come from the palette image.

### Effects are different

Fire, water, wind and other effects are normal full-colour images: paint them any colour, no palette. They must
keep the exact size they were exported with, or they are rejected.

## Repaint frames

1. Open the move and click **Export move**. Leave *Also export the palette as a PNG* ticked. A folder with the
   frames opens.
2. Paint over the PNGs with colours from the palette image (see [How colours work](#how-colours-work)). Keep the
   **file names** and the **canvas size** the same.
3. Drag the PNGs onto the Frame Bender window, as many as you like at once. You get a before / after for each.
4. **Build & install**, test in Training mode, then **Restore original** when you are done.

<!-- GIF: export a move, paint in Photoshop, drag the PNGs back onto the window. -->

**Need more room**, for a hat or bigger hair? Click the small arrow next to Export move and set **Frame size** to
**Full screen**. Every PNG is then the whole game screen with the character at its real spot, so you can draw
anywhere around them.

**Want to see your new colours while painting?** Tick **Use my current colours** in the same menu. The frames are
exported in the costume you are editing instead of the default one.

## Painting tips

- **Only paint what you change.** Everything you leave untouched stays exactly as the game has it, outline
  included.
- **Use the eyedropper** on the palette image instead of picking colours by eye.
- **Pick clearly different rows** for new parts. A colour very close to one the frame already uses is treated as
  that one.
- **Outlines are black with a soft edge.** Use a soft (anti-aliased) brush; a hard pencil edge stays hard in game.
- **Keep transparent areas transparent.**
- **Leave masks alone.** Some effects have `…_mask` files that look empty; only change them if you know what they
  do.
- Each frame takes a couple of seconds to process after you drop it.

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
