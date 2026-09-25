"""Desktop launcher helpers: PATH, port choice, server probing, quit handling.

The GUI (pywebview) is never started here; ``listener.desktop`` imports it
lazily inside ``main()``.
"""

import http.server
import json
import os
import socket
import threading

from listener import desktop


def test_extend_path_adds_existing_dirs_once(tmp_path, monkeypatch):
    present = tmp_path / "bin"
    present.mkdir()
    monkeypatch.setattr(desktop, "EXTRA_PATH_DIRS", (str(present), str(tmp_path / "missing")))
    env = {"PATH": "/usr/bin"}
    assert desktop.extend_path(env) == f"{present}{os.pathsep}/usr/bin"
    assert desktop.extend_path(env) == f"{present}{os.pathsep}/usr/bin"  # idempotent


def test_closing_message_states_what_happens():
    assert desktop.closing_message({"recording": False, "run": False}) is None
    msg = desktop.closing_message({"recording": True, "run": False})
    assert "stopped and saved" in msg and msg.endswith("Quit Listener?")
    msg = desktop.closing_message({"recording": True, "run": True})
    assert "interrupted" in msg and "retry" in msg


class _FakeWindow:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def create_confirmation_dialog(self, title, message):
        self.asked.append((title, message))
        return self.answer


def test_on_closing_confirms_and_stops(monkeypatch):
    posted = []
    monkeypatch.setattr(desktop, "_post", lambda url, timeout=15.0: posted.append(url) or {})

    monkeypatch.setattr(desktop, "activity", lambda port: {"recording": False, "run": False})
    win = _FakeWindow(answer=False)
    assert desktop.on_closing(win, 8642) is True and win.asked == [] and posted == []

    monkeypatch.setattr(desktop, "activity", lambda port: {"recording": True, "run": True})
    win = _FakeWindow(answer=False)
    assert desktop.on_closing(win, 8642) is False and len(win.asked) == 1 and posted == []

    win = _FakeWindow(answer=True)
    assert desktop.on_closing(win, 8642) is True
    assert posted == ["http://127.0.0.1:8642/api/stop", "http://127.0.0.1:8642/api/queue/stop"]


def test_preferred_port_env_override():
    assert desktop.preferred_port({}) == desktop.DEFAULT_PORT
    assert desktop.preferred_port({desktop.ENV_PORT: "8646"}) == 8646
    assert desktop.preferred_port({desktop.ENV_PORT: "nope"}) == desktop.DEFAULT_PORT
    assert desktop.preferred_port({desktop.ENV_PORT: "70000"}) == desktop.DEFAULT_PORT


def test_pick_port_avoids_a_busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        assert desktop.port_free(port) is False
        chosen = desktop.pick_port(port)
        assert chosen != port and desktop.port_free(chosen)


def test_server_alive_requires_a_listener_status_payload():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"recording": {"active": True}, "run": {"active": False}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert desktop.wait_for_server(port, timeout=5) is True
        assert desktop.activity(port) == {"recording": True, "run": False}
    finally:
        srv.shutdown()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        assert desktop.server_alive(s.getsockname()[1]) is False
