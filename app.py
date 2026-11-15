"""Frame Bender: the local web server.

Run:   python app.py
Open:  http://127.0.0.1:8765

Serves on 127.0.0.1 only. All real work is in core.py and mods.py.
Every route works on one character: ?char=<name> (GET) or "char" in the body (POST).
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

import core
import mods
import skinpack
import stages

from paths import HOST, PORT
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

app = Flask(__name__, static_folder=str(core.APP_DIR / "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
_build_lock = threading.Lock()


@app.before_request
def local_only():
    # Other websites must not be able to drive this server through the browser.
    if request.host not in ALLOWED_HOSTS:
        abort(403)
    if request.method != "GET" and request.headers.get("X-Skin-Editor") != "1":
        abort(403)


@app.errorhandler(core.EditorError)
def editor_error(err):
    return jsonify({"error": str(err)}), 400


@app.after_request
def no_sniff(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.endswith((".js", ".css", ".html")) or request.path == "/":
        resp.headers["Cache-Control"] = "no-cache"      # always pick up editor updates
    return resp


@app.get("/")
def index():
    return app.send_static_file("index.html")


# ----------------------------------------------------------------- helpers

def _body():
    return request.get_json(silent=True) or {}


def _char():
    """The character this request is about (must be one of the paks in backup\\)."""
    name = request.args.get("char") or request.form.get("char") or _body().get("char")
    if not name:
        name = core.load_config()["character"]
    name = core.check_char(str(name).lower())
    if name not in {c["name"] for c in core.list_characters()}:
        raise core.EditorError(f"no character pak named {name} in the backup folder")
    return name


def _ws():
    return core.workspace(_char())


def _project():
    return mods.Project(core.load_config()["project"], _char())


def _locked(work):
    if not _build_lock.acquire(blocking=False):
        raise core.EditorError("a build is already running")
    try:
        return work()
    finally:
        _build_lock.release()


# ----------------------------------------------------------------- setup / status

@app.get("/api/characters")
def characters():
    cfg = core.load_config()
    chars = core.list_characters()
    for c in chars:
        c["edits"], c["colors"] = mods.project_summary(cfg["project"], c["name"])
        c["installed"] = bool(mods.installed_info(c["name"]))
    return jsonify({"characters": chars, "current": cfg["character"], "project": cfg["project"],
                    "game_dir": cfg["game_dir"], "game_dir_ok": Path(cfg["game_dir"]).is_dir()})


@app.get("/api/status")
def status():
    cfg = core.load_config()
    char = _char()
    out = {"character": char, "game_dir": cfg["game_dir"],
           "game_dir_ok": Path(cfg["game_dir"]).is_dir(),
           "backup_ok": False, "backup_error": None, "backup_hash": None,
           "cache_ready": False, "project": cfg["project"]}
    try:
        digest = core.verify_backup(char)
        out.update(backup_ok=True, backup_hash=digest,
                   cache_ready=core.cache_ready(char, digest))
    except core.EditorError as err:
        out["backup_error"] = str(err)
    state, game_hash = core.game_pak_state(char, cfg["game_dir"])
    out.update(game_pak=state, game_hash=game_hash, installed=mods.installed_info(char))
    out["edits"], out["colors"] = mods.project_summary(cfg["project"], char)
    return jsonify(out)


@app.post("/api/setup")
def setup():
    """Back up (only if missing), verify, and extract one character pak to the cache."""
    body = _body()
    cfg = core.load_config()
    if isinstance(body.get("game_dir"), str) and body["game_dir"].strip():
        cfg["game_dir"] = body["game_dir"].strip()
    if not Path(cfg["game_dir"]).is_dir():
        raise core.EditorError(f"game folder not found: {cfg['game_dir']}")
    char = core.check_char(str(body.get("char") or cfg["character"]).lower())
    created = core.create_backup_if_missing(char, cfg["game_dir"])
    cfg["character"] = char
    core.save_config(cfg)
    ws = core.workspace(char)
    return jsonify({"ok": True, "backup_created": created, "frames": len(ws.frames)})


# ----------------------------------------------------------------- browsing

@app.get("/api/moves")
def moves():
    ws = _ws()
    mods.warm(ws)                                   # get the palette rows ready in the background
    return jsonify(ws.tree_json())


@app.get("/api/move")
def move():
    groups = _ws().move_frames(request.args.get("category", ""),
                               request.args.get("move", ""))
    edits = _project().edits()
    for group in groups:
        for frame in group["frames"]:
            edit = edits.get(frame["name"])
            if edit:
                frame["edit"] = edit
    return jsonify(groups)


def _try_colors(text):
    """"3.0.12e2e2,3.2.ffffff" -> {(row, col): (r, g, b)}: colours to preview without saving."""
    out = {}
    for part in text.split(",")[:8]:
        bits = part.split(".")
        try:
            row, col, value = int(bits[0]), int(bits[1]), int(bits[2], 16)
        except (ValueError, IndexError):
            continue
        if 0 <= row < 64 and 0 <= col < 3 and len(bits[2]) == 6:
            out[(row, col)] = (value >> 16, (value >> 8) & 255, value & 255)
    return out


@app.get("/api/png")
def png():
    name = request.args.get("name", "")
    try:
        if request.args.get("palette"):             # preview with a (possibly edited) palette
            data = _project().render(_ws(), name, request.args["palette"],
                                     rows=request.args.get("rowmap") == "1",
                                     thumb=request.args.get("thumb") == "1",
                                     override=_try_colors(request.args.get("try", "")))
            resp = app.response_class(data, mimetype="image/png")
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if request.args.get("edited") == "1":       # what the game will show for an edit
            path = _project().preview_png(_ws(), name)
            return send_file(path, mimetype="image/png", max_age=0)
        path = _ws().png_file(name, thumb=request.args.get("thumb") == "1")
    except (ValueError, OSError) as err:
        raise core.EditorError(f"could not decode frame: {err}")
    return send_file(path, mimetype="image/png", max_age=3600)


# ----------------------------------------------------------------- scanning the game folder

@app.get("/api/find-game")
def find_game():
    """Read-only: where the game is installed (any drive, any Steam library)."""
    found = core.find_game_dirs(refresh=request.args.get("again") == "1")
    return jsonify({"found": [str(f) for f in found]})


@app.get("/api/progress")
def progress():
    """What a slow backup / unpack is doing right now: {step, done, total} or null."""
    name = str(request.args.get("of", "")).lower()
    return jsonify({"progress": core.progress_of(name) if core.CHAR_RE.fullmatch(name) else None})


@app.post("/api/game-dir")
def game_dir():
    """Remember where the game's data_packages folder is. Changes nothing in it."""
    folder = str(_body().get("game_dir", "")).strip().strip('"')
    if not folder or not Path(folder).is_dir():
        raise core.EditorError(f"That folder does not exist: {folder or '(empty)'}")
    if not any(Path(folder).glob("*.pak")):
        inner = Path(folder) / "data_packages"      # the game's own folder was picked: go one in
        if inner.is_dir() and any(inner.glob("*.pak")):
            folder = str(inner)
        else:
            raise core.EditorError("There are no .pak files in that folder. Pick the data_packages "
                                   "folder inside the game's folder.")
    cfg = core.load_config()
    cfg["game_dir"] = folder
    core.save_config(cfg)
    return jsonify({"ok": True, "game_dir": folder})


@app.post("/api/scan")
def scan():
    """Read-only: which character paks in the game folder are new or updated."""
    cfg = core.load_config()
    return jsonify({"game_dir": cfg["game_dir"], "paks": core.scan_game(cfg["game_dir"])})


def _scanned_name():
    """A character name that really is a character pak in the game folder."""
    cfg = core.load_config()
    name = core.check_char(str(_body().get("char", "")).lower())
    if core.character_info(Path(cfg["game_dir"]) / f"{name}.pak") is None:
        raise core.EditorError(f"{name}.pak is not a character pak in the game folder")
    return name, cfg["game_dir"]


@app.post("/api/backup")
def backup():
    """First backup of a character the game folder has and backup\\ does not."""
    name, game_dir = _scanned_name()
    created = core.create_backup_if_missing(name, game_dir)
    core.workspace(name)                              # verify + unpack right away
    return jsonify({"ok": True, "created": created})


@app.post("/api/refresh-backup")
def refresh_backup():
    """After a game update: keep the old backup in backup\\previous\\, back up the new pak."""
    name, game_dir = _scanned_name()
    result = _locked(lambda: core.refresh_backup(name, game_dir))
    core.workspace(name)
    return jsonify(result)


# ----------------------------------------------------------------- editing frames

@app.post("/api/export")
def export():
    body = _body()
    cfg = core.load_config()
    base = cfg["export_dirs"].get(cfg["project"])
    if base:                                        # the folder may have gone since it was chosen
        base = mods.check_export_folder(base, cfg["game_dir"])
    result = _project().export_move(_ws(), str(body.get("category", "")),
                                    str(body.get("move", "")),
                                    effects=bool(body.get("effects")), masks=bool(body.get("masks")),
                                    margin=int(body.get("margin") or 0),
                                    base=base, palette=bool(body.get("palette")),
                                    full=bool(body.get("full")), slot=str(body.get("slot") or "") or None)
    _open(result["folder"])
    return jsonify(result)


def _settings(cfg):
    return {"hide_export_rules": cfg["hide_export_rules"], "hide_install_notice": cfg["hide_install_notice"],
            "export_folder": cfg["export_dirs"].get(cfg["project"], ""),
            "default_export_folder": str(_project().work_dir)}


@app.get("/api/settings")
def settings():
    return jsonify(_settings(core.load_config()))


@app.post("/api/settings")
def settings_set():
    """hide_export_rules: true/false. export_folder: a folder for exported PNGs ("" = default)."""
    body, cfg = _body(), core.load_config()
    for key in ("hide_export_rules", "hide_install_notice"):
        if isinstance(body.get(key), bool):
            cfg[key] = body[key]
    if isinstance(body.get("export_folder"), str):
        if body["export_folder"].strip():
            cfg["export_dirs"][cfg["project"]] = str(
                mods.check_export_folder(body["export_folder"], cfg["game_dir"]))
        else:
            cfg["export_dirs"].pop(cfg["project"], None)
    core.save_config(cfg)
    return jsonify(_settings(cfg))


def _open(folder):
    if os.environ.get("SKIN_EDITOR_NO_OPEN"):
        return
    try:
        os.startfile(folder)                        # show the folder in Explorer
    except (OSError, AttributeError):
        pass


@app.post("/api/replace")
def replace():
    """One dropped PNG. Matched to a frame by its file name."""
    upload = request.files.get("file")
    if upload is None:
        raise core.EditorError("no file received")
    data = upload.read(mods.MAX_PNG_BYTES + 1)
    return jsonify(_project().replace(_ws(), upload.filename or "", data))


@app.post("/api/revert")
def revert():
    """Revert one frame ("name") or several ("names", up to 5000) in one go."""
    body = _body()
    names = body.get("names")
    if isinstance(names, list):
        count = _project().revert_many([str(n) for n in names[:5000]])
        return jsonify({"ok": True, "reverted": count})
    _project().revert(str(body.get("name", "")))
    return jsonify({"ok": True, "reverted": 1})


# ----------------------------------------------------------------- game stages (backdrops)

@app.get("/api/stages")
def stage_list():
    cfg = core.load_config()
    found = stages.list_stages(cfg["game_dir"])
    return jsonify({"stages": found, "home": stages.home_stage(_char(), found),
                    "anchor": stages.anchor(_ws())})


@app.get("/api/stage")
def stage_image():
    cfg = core.load_config()
    try:
        path, meta = stages.render(cfg["game_dir"], request.args.get("id", ""))
    except OSError as err:
        raise core.EditorError(f"could not read the stage: {err}")
    if request.args.get("meta") == "1":
        return jsonify(meta)
    return send_file(path, mimetype="image/png", max_age=3600)


@app.get("/api/edited")
def edited():
    """Everything the project changes for this character (frames + palette colors)."""
    return jsonify(_project().edited_summary(_ws()))


@app.post("/api/open-folder")
def open_folder():
    path = _project().folder(str(_body().get("which", "")))
    _open(str(path))
    return jsonify({"folder": str(path)})


# ----------------------------------------------------------------- skin packs (sharing)

def _reveal(path):
    if os.environ.get("SKIN_EDITOR_NO_OPEN"):          # tests: do not pop up Explorer windows
        return
    try:
        subprocess.Popen(["explorer", f"/select,{path}"])   # open Explorer with the file selected
    except OSError:
        pass


@app.post("/api/skin/export")
def skin_export():
    body = _body()
    result = skinpack.create(_project(), _ws(), body.get("name"), body.get("author", ""),
                             body.get("description", ""), frames=bool(body.get("frames", True)),
                             palettes=bool(body.get("palettes", True)))
    if body.get("reveal", True):
        _reveal(result["path"])
    return jsonify(result)


@app.get("/api/skin/download")
def skin_download():
    """Download a pack that was exported earlier (only files directly inside exports/)."""
    name = request.args.get("name", "")
    if not name or name != Path(name).name or not name.lower().endswith(".zip"):
        abort(404)
    path = mods.EXPORT_DIR / name
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/zip")


@app.post("/api/skin/import")
def skin_import():
    """mode=check: what would happen (changes nothing). mode=apply: import into the project."""
    upload = request.files.get("file")
    if upload is None:
        raise core.EditorError("no file received")
    data = upload.read(skinpack.MAX_PACK + 1)
    ws, project = _ws(), _project()
    if request.form.get("mode") == "apply":
        try:
            slot_map = json.loads(request.form.get("slot_map") or "{}")
        except ValueError:
            slot_map = {}
        result = skinpack.apply(data, ws, project, overwrite=request.form.get("overwrite") == "1",
                                slot_map=slot_map)
        return jsonify(result)
    return jsonify(skinpack.analyze(data, ws, project))


# ----------------------------------------------------------------- palettes

@app.get("/api/palettes")
def palettes():
    ws, changed = _ws(), _project().palette_edits()
    return jsonify({
        "default": ws.default_palette["name"] if ws.default_palette else None,
        "row_names": core.row_names(ws.char),
        "palettes": [{"name": name, "colors": len(changed.get(name, {})),
                      "locked": record["access"] or ""}
                     for name, record in ws.costume.items()],
    })


@app.get("/api/palette")
def palette():
    ws, project, name = _ws(), _project(), request.args.get("name", "")
    table = project.palette_table(ws, name)
    original = ws.costume[name]["palette"]
    return jsonify({
        "name": name,
        "rows": [[cell[:3] for cell in row[:3]] for row in table],
        "original": [[list(cell[:3]) for cell in row[:3]] for row in original],
    })


@app.post("/api/palette")
def palette_set():
    body = _body()
    project, name = _project(), str(body.get("name", ""))
    if body.get("reset_all"):
        if name not in _ws().costume:
            raise core.EditorError(f"unknown costume palette: {name}")
        project.reset_palette(name)
    elif isinstance(body.get("cells"), list):       # several colours at once: [[row, col, rgb|null]]
        ws, cells = _ws(), {}
        if name not in ws.costume:
            raise core.EditorError(f"unknown costume palette: {name}")
        for item in body["cells"][:192]:
            try:
                row, col, rgb = item
                if rgb is None:                     # null = the original colour
                    rgb = ws.costume[name]["palette"][row][col][:3]
                rgb = tuple(int(v) for v in rgb)
                ok = (isinstance(row, int) and isinstance(col, int) and 0 <= row < 64
                      and 0 <= col < 3 and len(rgb) == 3 and all(0 <= v <= 255 for v in rgb))
            except (TypeError, ValueError, IndexError):
                ok = False
            if not ok:
                raise core.EditorError("invalid color")
            cells[(row, col)] = rgb
        project.set_colors(ws, name, cells)
    else:
        project.set_color(_ws(), name, body.get("row"), body.get("col"), body.get("color"))
    return jsonify({"ok": True, "colors": project.palette_color_count()})


@app.get("/api/rows")
def rows():
    return jsonify(mods.move_rows(_ws(), request.args.get("category", ""),
                                  request.args.get("move", "")))


# ----------------------------------------------------------------- build / install

@app.post("/api/rebuild-check")
def rebuild_check():
    """Rebuild with no edits and compare with the original; also round-trip the palettes."""
    char = _char()

    def work():
        out = core.BUILD_DIR / f"{char}.check.pak"
        digest, count = core.build_pak(char, out)
        want = core.expected_hash(char)
        out.unlink(missing_ok=True)
        palettes_same, palette_count = mods.sprbin_roundtrip(core.workspace(char))
        return jsonify({"files": count, "hash": digest, "expected": want,
                        "identical": digest == want,
                        "palettes_identical": palettes_same, "palettes": palette_count})
    return _locked(work)


@app.post("/api/install")
def install():
    project, ws = _project(), _ws()
    return _locked(lambda: jsonify(mods.install(project, ws, core.load_config()["game_dir"])))


@app.post("/api/restore")
def restore():
    char = _char()
    return _locked(lambda: jsonify(mods.restore(char, core.load_config()["game_dir"])))


if __name__ == "__main__":
    if "--port" in sys.argv:                      # a second copy for testing, e.g. --port 8766
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
        ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    print(f"Frame Bender is running. Open http://{HOST}:{PORT} in your browser. Ctrl+C to stop.")
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
