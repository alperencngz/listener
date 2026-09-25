"""Entry point for the Listener.app bundle (py2app, alias mode).

Launched by macOS LaunchServices, not a terminal, so there is no console:
stdout/stderr go to ~/.listener/listener.log. The bundle exists so macOS ties
the Microphone permission to a stable app identity (io.github.alperencngz.listener)
instead of to whichever terminal happened to start the server.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    log_dir = Path.home() / ".listener"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(log_dir)  # never leave stray files in Contents/Resources
    # Line-buffered, utf-8: a Finder-launched process has no shell locale, and
    # Turkish transcript text in a print() must not raise UnicodeEncodeError.
    log = open(log_dir / "listener.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = log
    sys.stderr = log
    print(f"\n[listener] === launch {datetime.now().isoformat(timespec='seconds')} ===")

    # py2app sets sys.frozen; multiprocessing then tries to re-exec the GUI stub.
    # Deleting it restores the normal spawn path (see Dictator's launcher).
    if getattr(sys, "frozen", None):
        del sys.frozen

    from listener.desktop import main as desktop_main

    try:
        code = desktop_main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    sys.exit(code)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
