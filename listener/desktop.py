"""Desktop launcher: the Listener web app inside a native macOS window.

Same code, same config file and the same database as ``listener web``. What
differs is only the packaging around it:

* the Flask server runs in a background thread on 127.0.0.1 and the UI is shown
  in a pywebview window (WebKit), so there is no browser tab and no terminal;
* meeting files default to ``~/Documents/Listener/transcripts`` (``data_dir`` in
  ``~/.listener/config.yaml`` or ``LISTENER_DATA_DIR`` override it);
* the Whisper model preselected on first run is ``large-v3-turbo`` instead of
  ``large-v3`` (a saved ``default_model`` still wins);
* closing the window quits the app. If a recording is in progress it is stopped
  and saved first; a running transcription is interrupted and can be retried;
* downloads and exports open a native Save dialog (WebKit has no silent
  download), defaulting to the Downloads folder.

Nothing here talks to the microphone or to Claude itself; all of that stays in
the web app. ``webview`` is imported lazily so this module can be tested
without a GUI.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8642
WINDOW_TITLE = "Listener"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (900, 600)
DESKTOP_DEFAULT_MODEL = "large-v3-turbo"
LOG_PATH = Path.home() / ".listener" / "listener.log"

# Finder launches an app with a minimal PATH. These are the usual homes of
# ffmpeg (imports) and the Claude Code CLI (analysis / memory in login mode).
EXTRA_PATH_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/.claude/local",
    "~/.claude/local/bin",
)

SMOKE_ENV = "LISTENER_DESKTOP_SMOKE"


# ---------------------------------------------------------------------------
# Small helpers (pure, unit-tested)
# ---------------------------------------------------------------------------

def extend_path(env: dict | None = None) -> str:
    """Prepend the well-known tool directories that exist; idempotent."""
    env = os.environ if env is None else env
    current = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    additions = []
    for raw in EXTRA_PATH_DIRS:
        path = os.path.expanduser(raw)
        if path not in current and path not in additions and os.path.isdir(path):
            additions.append(path)
    env["PATH"] = os.pathsep.join(additions + current)
    return env["PATH"]


def default_data_dir() -> Path:
    return Path.home() / "Documents" / "Listener"


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _get_json(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _post(url: str, timeout: float = 15.0) -> dict | None:
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def server_alive(port: int, timeout: float = 0.5) -> bool:
    """True when a Listener server answers on this port (not just any process)."""
    status = _get_json(f"{url_for(port)}/api/status", timeout=timeout)
    return isinstance(status, dict) and "recording" in status


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    """The preferred port when free, otherwise one the OS hands out."""
    if port_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(port):
            return True
        time.sleep(0.15)
    return False


def activity(port: int) -> dict:
    """What is going on right now: {"recording": bool, "run": bool}."""
    status = _get_json(f"{url_for(port)}/api/status") or {}
    rec = status.get("recording") or {}
    run = status.get("run") or {}
    return {"recording": bool(rec.get("active")), "run": bool(run.get("active"))}


def closing_message(state: dict) -> str | None:
    """Plain-language confirmation text, or None when nothing is in progress."""
    parts = []
    if state.get("recording"):
        parts.append("A recording is in progress. It will be stopped and saved.")
    if state.get("run"):
        parts.append("A transcription is running. It will be interrupted; you can retry it later.")
    if not parts:
        return None
    return " ".join(parts) + " Quit Listener?"


def on_closing(window, port: int) -> bool:
    """pywebview ``closing`` handler: confirm when busy, then stop cleanly."""
    state = activity(port)
    message = closing_message(state)
    if message and not window.create_confirmation_dialog("Quit Listener?", message):
        return False
    if state.get("recording"):
        _post(f"{url_for(port)}/api/stop")
    if state.get("run"):
        _post(f"{url_for(port)}/api/queue/stop")
    return True


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

def start_server(port: int) -> threading.Thread:
    from listener.web.app import run

    thread = threading.Thread(target=run, kwargs={"port": port}, name="listener-web", daemon=True)
    thread.start()
    return thread


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    try:
        # In the app bundle stderr already points at the log file; only echo to a real terminal.
        if sys.stderr.isatty():
            handlers.append(logging.StreamHandler(sys.stderr))
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers, force=True)


def _smoke(window, port: int) -> None:
    """Self-check used by the build script: read the loaded page, log, quit."""
    time.sleep(3.0)
    result = {"url": url_for(port)}
    try:
        result["title"] = window.evaluate_js("document.title")
        result["record_button"] = bool(window.evaluate_js("!!document.querySelector('#rec-btn')"))
        result["sessions"] = window.evaluate_js("document.querySelectorAll('#slist .sitem').length")
        result["settings_loaded"] = bool(window.evaluate_js("!!document.querySelector('#model option')"))
        result["status_ok"] = server_alive(port)
        if result["sessions"]:
            # Open the first meeting and make sure its transcript renders in WebKit.
            window.evaluate_js("document.querySelector('#slist .sitem').click()")
            time.sleep(2.0)
            result["transcript_lines"] = window.evaluate_js("document.querySelectorAll('#content .ts').length")
            result["download_links"] = window.evaluate_js("document.querySelectorAll('a.dl-btn').length")
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    logger.info("[smoke] %s", json.dumps(result))
    window.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    setup_logging()
    extend_path()
    from listener import settings
    import listener.web.app as webapp

    root = settings.resolve_data_dir(default_data_dir())
    transcripts = root / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    webapp.set_output_dir(transcripts)
    os.environ.setdefault(settings.ENV_DEFAULT_MODEL, DESKTOP_DEFAULT_MODEL)
    logger.info("Listener desktop starting; meetings in %s; config %s", transcripts, settings.CONFIG_PATH)

    port = DEFAULT_PORT
    if server_alive(port):
        logger.info("A Listener server is already running on port %d; opening a window to it", port)
    else:
        port = pick_port(port)
        start_server(port)
        if not wait_for_server(port):
            logger.error("The web server did not start within 30s; see %s", LOG_PATH)
            return 1

    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True  # downloads/exports open a Save dialog (defaults to ~/Downloads)
    window = webview.create_window(
        WINDOW_TITLE, url_for(port), width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
    )
    window.events.closing += lambda: on_closing(window, port)
    if os.environ.get(SMOKE_ENV):
        webview.start(_smoke, (window, port))
    else:
        webview.start()
    logger.info("Window closed; Listener desktop exiting")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
