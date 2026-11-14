"""Where things live. Imports nothing from the rest of the app, so the launcher can use it
before core.py (which needs the tools) is imported.

APP_DIR  - the code and static/ (the .exe's bundle when packaged)
DATA_DIR - everything the user creates: backups, cache, projects, exports, settings.
           Same folder as the code when run from source; %LOCALAPPDATA%\\FrameBender for the
           .exe (its own folder may not be writable). Override: SKIN_EDITOR_DATA.
           The app used to be called "Avatar Legends Skin Editor": a data folder with that
           name is renamed to FrameBender on the first start (nothing in it is changed).
"""
import os
import sys
from pathlib import Path

import toolsfetch

HOST = "127.0.0.1"
PORT = 8765

FROZEN = bool(getattr(sys, "frozen", False))          # running as the packaged .exe
APP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
if os.environ.get("SKIN_EDITOR_DATA"):
    DATA_DIR = Path(os.environ["SKIN_EDITOR_DATA"])
elif FROZEN:
    _base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    DATA_DIR, _old = _base / "FrameBender", _base / "AvatarLegendsSkinEditor"
    if _old.is_dir() and not DATA_DIR.exists():
        try:
            _old.rename(DATA_DIR)                      # a rename: backups, projects, settings all move as they are
        except OSError:
            DATA_DIR = _old                            # in use (an older copy is open): keep using it for now
else:
    DATA_DIR = APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Where a download or a manual copy of the tools goes.
TOOLS_DOWNLOAD_DIR = DATA_DIR / "avatar-legends-tools"


def tool_dirs():
    """Folders searched for avatar-legends-tools, in order."""
    found = []
    if os.environ.get("SKIN_EDITOR_TOOLS"):
        found.append(Path(os.environ["SKIN_EDITOR_TOOLS"]))
    found += [APP_DIR / "avatar-legends-tools", APP_DIR.parent / "avatar-legends-tools"]
    if FROZEN:
        found.append(Path(sys.executable).resolve().parent / "avatar-legends-tools")
    found.append(TOOLS_DOWNLOAD_DIR)
    unique = []                                        # no folder listed twice
    for folder in found:
        if folder not in unique:
            unique.append(folder)
    return unique


def find_tools():
    """The folder holding all the tool scripts, or None."""
    return toolsfetch.find(tool_dirs())
