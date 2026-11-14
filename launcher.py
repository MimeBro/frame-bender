"""Frame Bender's desktop launcher: starts the local server and shows the editor in its own window.

    python launcher.py            window (needs `pip install pywebview` and the Edge WebView2 runtime)
    python launcher.py --browser  use your normal browser instead of a window (Ctrl+C to stop)
    python launcher.py --check    start, test the server, exit (used to test the packaged .exe)

Options (also work on the .exe, for example in a shortcut):
    --data <folder>     keep projects, cache and settings in this folder
    --backup <folder>   folder with the untouched backup paks

If the original modding tools (avatar-legends-tools) are missing, a setup screen explains
what is missing and offers a download button or manual steps. Nothing is downloaded until
the user clicks that button.

If the window cannot open (Windows blocked files of a downloaded zip, or WebView2 is missing),
the editor opens in the normal browser instead and a small box stays up to close it.

This is also the entry point of the packaged FrameBender.exe (see build_exe.py).
"""
import ctypes
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TITLE = "Frame Bender"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def message(text, error=True):
    """A message box (there is no console in the packaged .exe)."""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, TITLE, 0x10 if error else 0x40)
    except Exception:
        print(text, file=sys.stderr)


def apply_path_options(argv):
    """--data / --backup set the same environment variables the app reads (before it is imported)."""
    for flag, variable in (("--data", "SKIN_EDITOR_DATA"), ("--backup", "SKIN_EDITOR_BACKUP")):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            os.environ[variable] = argv[argv.index(flag) + 1]


def redirect_output_if_needed():
    """A windowed .exe has no stdout/stderr; send them to a log file so errors are not lost."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import paths
    log = open(paths.DATA_DIR / "launcher.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = log
    print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start ---")


def answers(url, path):
    try:
        with urllib.request.urlopen(url + path, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def already_running(url):
    """Another copy of the editor (or its setup screen) already owns the port."""
    return answers(url, "/api/characters") or answers(url, "/api/setup-status")


# ----------------------------------------------------------------- setup screen (tools missing)

class SetupServer:
    """A tiny server shown while avatar-legends-tools is missing. Same address as the editor,
    so the window just reloads into the real editor once the files are in place."""

    def __init__(self, on_ready):
        import paths
        import toolsfetch
        self.paths, self.tf, self.on_ready = paths, toolsfetch, on_ready
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                print("setup:", fmt % args)

            def _send(self, code, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind + ("; charset=utf-8" if kind != "application/json" else ""))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self):
                return self.headers.get("Host") in (f"{paths.HOST}:{paths.PORT}", f"localhost:{paths.PORT}")

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                if self.path in ("/", "/index.html", "/setup.html"):
                    page = (paths.APP_DIR / "static" / "setup.html").read_bytes()
                    return self._send(200, page, "text/html")
                if self.path == "/style.css":            # the editor's own look
                    sheet = (paths.APP_DIR / "static" / "style.css").read_bytes()
                    return self._send(200, sheet, "text/css")
                if self.path == "/api/setup-status":
                    return self._send(200, outer.status())
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._allowed() or self.headers.get("X-Skin-Editor") != "1":
                    return self._send(403, {"error": "forbidden"})
                route = {"/api/setup-download": outer.download, "/api/setup-open": outer.open_folder,
                         "/api/setup-continue": outer.proceed}.get(self.path)
                if route is None:
                    return self._send(404, {"error": "not found"})
                try:
                    self._send(200, route())
                except Exception as err:                 # always answer with a readable message
                    self._send(200, {"error": str(err)})

        class Server(ThreadingHTTPServer):
            allow_reuse_address = False                  # never share the port with another program
            daemon_threads = True

        self.server = Server((paths.HOST, paths.PORT), Handler)

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def status(self):
        tf, paths = self.tf, self.paths
        folder = paths.find_tools() or paths.TOOLS_DOWNLOAD_DIR
        states = tf.file_states(folder)
        return {
            "ready": paths.find_tools() is not None,
            "download_dir": str(paths.TOOLS_DOWNLOAD_DIR),
            "looked_in": [str(p) for p in paths.tool_dirs()],
            "files": [{"name": n, "state": s, "purpose": tf.FILE_PURPOSE[n], "url": tf.source_url(n)}
                      for n, s in states.items()],
        }

    def download(self):
        try:
            downloaded, kept = self.tf.ensure_tools(self.paths.TOOLS_DOWNLOAD_DIR)
        except self.tf.ToolsError as err:
            return {"error": str(err)}
        return {"ok": True, "downloaded": downloaded, "kept": kept}

    def open_folder(self):
        folder = self.paths.TOOLS_DOWNLOAD_DIR
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))
        return {"ok": True}

    def proceed(self):
        if self.paths.find_tools() is None:
            return {"error": "The four scripts are not all in place yet."}
        threading.Thread(target=self.on_ready, daemon=True).start()
        return {"ok": True}


# ----------------------------------------------------------------- the editor

def start_editor_server():
    """Import the editor and serve it. Raises SystemExit(message) if something is missing."""
    try:
        import app as web
    except ImportError as err:
        name = getattr(err, "name", None) or str(err)
        raise SystemExit(f"A Python package Frame Bender needs is missing: {name}\n\n"
                         "Install the requirements with:\n    python -m pip install -r requirements.txt")
    from werkzeug.serving import make_server
    server = make_server(web.HOST, web.PORT, web.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Running:
    """Whichever server is currently on the port (setup screen first, then the editor)."""

    def __init__(self):
        self.setup = None
        self.editor = None

    def switch_to_editor(self):
        if self.setup:
            self.setup.stop()
            self.setup = None
        last = None
        for _ in range(10):                               # the port may need a moment to free up
            try:
                self.editor = start_editor_server()
                return
            except OSError as err:
                last = err
                time.sleep(0.3)
            except SystemExit as err:
                print("editor failed to start:", err)
                return
        print("editor failed to start:", last)

    def stop(self):
        for server in (self.setup, self.editor):
            if server:
                try:
                    server.stop() if isinstance(server, SetupServer) else server.shutdown()
                except Exception:
                    pass


class WindowApi:
    """What the page may ask the window for (window.pywebview.api.*). Only a folder picker:
    a web page cannot open one itself. The chosen path is still checked by the server."""

    def choose_folder(self):
        import webview
        if not webview.windows:
            return ""
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:
            kind = webview.FOLDER_DIALOG                  # older pywebview
        picked = webview.windows[0].create_file_dialog(kind)
        return str(picked[0]) if picked else ""


def show_window(url):
    import webview
    import paths
    webview.settings["ALLOW_DOWNLOADS"] = True          # the skin pack "Download" link
    webview.create_window(TITLE, url, width=1360, height=900, min_size=(960, 640), js_api=WindowApi())
    # private_mode=False + a storage path keeps the page's small settings (last tab, author)
    webview.start(private_mode=False, storage_path=str(paths.DATA_DIR / "webview"))


def use_browser_instead(url, err):
    """The window could not open: show the editor in the normal browser. There is no window to
    close then, so a message box stays up and closing it stops Frame Bender."""
    import webbrowser
    webbrowser.open(url)
    message("The Frame Bender window could not open on this PC, so Frame Bender opened in your "
            "browser instead. It works the same there.\n\n"
            "Leave this box open while you work. Click OK to close Frame Bender.\n\n"
            "To get the window back: the usual cause is that Windows blocked the files of the "
            "downloaded zip. Delete the unzipped folder, right-click the zip, choose Properties, "
            "tick Unblock, click OK, and unzip it again. If that does not help, install the "
            f"Microsoft Edge WebView2 Runtime: {WEBVIEW2_URL}\n\n"
            f"Details: {err}", error=False)


def main(argv):
    apply_path_options(argv)
    running = Running()
    try:
        redirect_output_if_needed()
        import paths
        url = f"http://{paths.HOST}:{paths.PORT}"
        if already_running(url):                          # open another window on the running copy
            pass
        elif paths.find_tools() is None:
            if "--check" in argv:
                print("check: tools missing (setup screen would be shown)")
                return 2
            running.setup = SetupServer(running.switch_to_editor)
            running.setup.start()
        else:
            running.editor = start_editor_server()
    except SystemExit as err:
        message(str(err) or "Frame Bender could not start.")
        return 1
    except OSError as err:
        message(f"Frame Bender could not start its local server on port 8765 ({err}).\n"
                "Another program may be using that port.")
        return 1
    except Exception as err:
        message(f"Frame Bender could not start:\n{err}")
        raise

    if "--check" in argv:
        ok = answers(url, "/api/characters")
        print("check:", "OK" if ok else "FAILED", url)
        running.stop()
        return 0 if ok else 1

    try:
        if "--browser" in argv:
            import webbrowser
            webbrowser.open(url)
            print(f"Frame Bender is running at {url}. Press Ctrl+C to stop.")
            while True:
                time.sleep(3600)
        else:
            show_window(url)
    except KeyboardInterrupt:
        pass
    except Exception as err:
        import paths
        if isinstance(err, ImportError) and not paths.FROZEN:
            message("The window component (pywebview) is not installed.\nRun:  python -m pip install pywebview\n"
                    "or start Frame Bender with run.bat to use your browser.")
            return 1
        import traceback
        traceback.print_exc()                            # into launcher.log for the packaged app
        use_browser_instead(url, err)
    finally:
        running.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
