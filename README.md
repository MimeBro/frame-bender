# Frame Bender

A skin editor for **Avatar Legends: The Fighting Game** (PC / Steam). Recolour costumes with a live preview,
repaint frames in your own art program, and put the result in your game with one click. One more click puts the
original back.

<!-- Add a screenshot or GIF here: the palette editor, a recoloured character in game. -->

> Unofficial fan project, not affiliated with the game's developers or publisher. No game files are included:
> it works on your own copy. Tested in online matches without issues; as with any mod, the developers could
> change their rules at any time, so use it at your own risk.

## Features

- **Every character and support**, playing on the game's own stages.
- **Palette editor**: click a colour, drag, watch the character change. Rows are labelled (Skin, Hair, Pants…),
  pointing at a row highlights what it colours, and one button works out matching shadow and light.
- **Repaint frames**: export a move as PNGs, paint in Photoshop or anything else, drop them back. Extra canvas
  space for hats and hair.
- **Install and restore**: one click puts your mod in the game, one click puts the original back. An untouched
  backup is always made first. Install several characters at once.
- **Skin packs**: share a small zip with only your edits, or import someone else's.

## Download

1. Get the zip from the [latest release](https://github.com/MimeBro/frame-bender/releases/latest).
2. **Before unzipping**: right-click the zip → **Properties** → tick **Unblock** → OK. Then unzip it anywhere.
3. Run `FrameBender.exe`. Windows may say it "protected your PC" because the app is not code-signed: choose
   **More info → Run anyway**.

Windows only for now.

## First run

1. **Tool scripts**: Frame Bender needs four small scripts from
   [avatar-legends-tools](https://github.com/calebrisc/avatar-legends-tools) that it is not allowed to include.
   Click **Download the 4 scripts** (each file is checked), or copy them in by hand as the screen explains.
2. **Game folder**: found automatically in your Steam libraries. If not, use **Browse…** to pick the game's
   `data_packages` folder.
3. **Back up characters**: click **Back up all**, or pick the ones you want. If you used other mods before,
   verify the game files in Steam first, so the backup is the stock file.
4. Pick a character and start editing. Test your mod in Training mode.

## Learn more

The [guide](docs/guide.md) covers recolouring and repainting step by step, painting tips, sharing skins, error
messages, game updates, running from source and building the app.

## Credits

Built on [avatar-legends-tools](https://github.com/calebrisc/avatar-legends-tools) by calebrisc, who worked out
the game's file formats. Frame Bender uses four of those scripts unmodified; they are never copied into this
repository.

## License

MIT for the code in this repository (see `LICENSE`). Game assets belong to their owners and are not part of this
repository.
