"""Locate, check and (only when the user asks) download the avatar-legends-tools scripts.

avatar-legends-tools (github.com/calebrisc/avatar-legends-tools) is not bundled with this
project: it has no license that allows redistribution, so the app links to it instead.
In a normal checkout it is a git submodule. Without it (the packaged .exe, or a clone made
without --recursive) the app shows a setup screen. NOTHING IS DOWNLOADED AUTOMATICALLY:
ensure_tools() runs only when the user clicks the download button on that screen. It fetches
the files from the original repository at a fixed commit and checks every file against a
SHA256 stored here, so nothing that was not reviewed is saved.

To move to a newer upstream version: update COMMIT and the hashes
(git show <commit>:<file> | sha256sum) after reading the changes.
"""
import hashlib
import urllib.error
import urllib.request
from pathlib import Path

REPO = "calebrisc/avatar-legends-tools"
COMMIT = "b333636d47fe739f52974b774334af5c997ff2c2"
FILES = {                                   # SHA256 of the file contents at COMMIT
    "munged_extract.py": "7a6d19be8519b386e8bfd58b9e606aee92594fa305e2b13806519e69eae4918f",
    "pak_extract.py": "c195941b499406601345f7cdfc350d4dca9508afabb800a932695972fa3448a1",
    "munged_encode.py": "1a9fe6d37ef5b7484da8a92170dac827b5b8ca6bf925700e399a0cc868f35860",
    "type3_encode.py": "abbd7393eb7291200dc85d5438566885564df5e345be45efdf19c324f5664669",
}
MAX_BYTES = 1024 * 1024


REPO_URL = f"https://github.com/{REPO}"
FILE_PURPOSE = {
    "munged_extract.py": "reads the game's image files and palettes",
    "pak_extract.py": "unpacks the game's .pak archives",
    "munged_encode.py": "writes effect images back into the game's format",
    "type3_encode.py": "writes character images back into the game's format",
}


class ToolsError(RuntimeError):
    pass


def source_url(name):
    return f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{name}"


def file_states(folder):
    """{name: 'ok' | 'different' | 'missing'} for one folder.

    'different' = present but not byte-identical to the version this app was tested with
    (for example a newer upstream version, or Windows line endings). It may still work.
    """
    states = {}
    for name, digest in FILES.items():
        path = Path(folder) / name
        if not path.is_file():
            states[name] = "missing"
        else:
            states[name] = "ok" if _good(path, digest) else "different"
    return states


def find(folders):
    """First folder that contains every tool script, or None."""
    for folder in folders:
        if all((Path(folder) / name).is_file() for name in FILES):
            return Path(folder)
    return None


def missing_report(folders, download_dir):
    """A plain-language explanation of what is missing and how to fix it."""
    lines = [
        "The editor needs avatar-legends-tools by calebrisc, and could not find it.",
        "",
        "Missing files (looked for all of them in each folder below):",
        *[f"  - {name}  ({purpose})" for name, purpose in FILE_PURPOSE.items()],
        "",
        "Folders searched:",
        *[f"  - {folder}" for folder in folders],
        "",
        "How to fix it (pick one):",
        f"  1. If you cloned this project with git, run:  git submodule update --init",
        f"  2. Download {REPO_URL} (Code > Download ZIP) and copy the four files above into:",
        f"       {download_dir}",
        "  3. Start the editor with launcher.py or the .exe: its setup screen can download the four",
        "     files for you when you click the button (nothing is downloaded before that).",
    ]
    return "\n".join(lines)


def _good(path, digest):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


def ensure_tools(dest):
    """Download the scripts that are MISSING from dest; files already there are never touched
    (they may be a version the user chose). Returns (downloaded names, kept names)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    downloaded, kept = [], []
    for name, digest in FILES.items():
        target = dest / name
        if target.exists():
            kept.append(name)
            continue
        url = source_url(name)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "avatar-legends-skin-editor"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError) as err:
            raise ToolsError(
                f"Could not download {name} from github.com/{REPO}: {err}\n"
                f"Download the repository from https://github.com/{REPO} and put its files in:\n{dest}"
            ) from err
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ToolsError(f"{name} downloaded from github.com/{REPO} did not match the expected "
                             "checksum, so it was not used.")
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        downloaded.append(name)
    return downloaded, kept
