"""Build the Windows app: dist/FrameBender/FrameBender.exe

    python -m pip install -r requirements-build.txt
    python build_exe.py

The result is a folder (zip it to share). It opens in its own window (Edge WebView2) and
keeps your data in %LOCALAPPDATA%\\FrameBender.

avatar-legends-tools is deliberately NOT included: it has no license allowing redistribution.
On first run the app shows a setup screen; the user can download the four scripts it needs
from the original repository (pinned commit, SHA256-verified, see toolsfetch.py) or copy them
in by hand. Nothing is downloaded without that click. Do not add the scripts to this build
unless the original author licenses them for redistribution.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "FrameBender"

# The tool scripts are loaded from disk at run time, so PyInstaller cannot see which
# standard-library modules they need. List them here.
TOOLS_STDLIB = ["argparse", "binascii", "dataclasses", "zlib", "math"]


def patch_python_3_10_0():
    """Python 3.10.0 has a bug in dis (fixed in 3.10.1) that crashes PyInstaller's analysis.

    Working around it makes the build possible, but a current Python (3.10.11+, 3.11 or
    3.12) is the better choice for builds you publish.
    """
    if sys.version_info[:3] != (3, 10, 0):
        return
    import dis
    original = dis._get_const_info

    def tolerant(const_index, const_list):
        try:
            return original(const_index, const_list)
        except IndexError:
            return None, "<unknown>"
    dis._get_const_info = tolerant
    print("Note: Python 3.10.0 detected; applying a workaround for its dis bug. "
          "Prefer Python 3.10.11 or newer for release builds.")


def main():
    args = [
        "--noconfirm", "--clean", "--windowed",
        "--name", NAME,
        "--icon", str(HERE / "assets" / "icon.ico"),          # the cabbage: exe, taskbar and window icon
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build" / "pyinstaller"),
        "--specpath", str(HERE / "build" / "pyinstaller"),
        "--add-data", f"{HERE / 'static'};static",
        "--add-data", f"{HERE / 'rows_known.json'};.",      # palette rows per character (make_rows_known.py)
        "--paths", str(HERE),
    ]
    for module in TOOLS_STDLIB:
        args += ["--hidden-import", module]
    args.append(str(HERE / "launcher.py"))
    patch_python_3_10_0()
    import PyInstaller.__main__
    PyInstaller.__main__.run(args)
    exe = HERE / "dist" / NAME / f"{NAME}.exe"
    print(f"\nBuilt {exe}" if exe.is_file() else "\nBuild finished but the .exe was not found.")


if __name__ == "__main__":
    main()
