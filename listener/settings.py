"""User settings shared by the CLI, the web app and the desktop app.

Everything lives in ``~/.listener/config.yaml``, the same file the webhooks and
the HuggingFace token already use. No key is required: every accessor has a
default, so a fresh machine works without a config file.

Keys read here
--------------
``data_dir``           folder that holds the ``transcripts`` directory (recordings,
                       transcripts, analyses, memory files). Env ``LISTENER_DATA_DIR``
                       overrides it.
``default_model``      Whisper model preselected in the UI. Env
                       ``LISTENER_DEFAULT_MODEL`` is the fallback when unset.
``claude_auth``        ``max`` = the Claude Code login on this machine,
                       ``api`` = an Anthropic API key.
``anthropic_api_key``  the key used when ``claude_auth`` is ``api``.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

CONFIG_PATH = Path.home() / ".listener" / "config.yaml"

ENV_DATA_DIR = "LISTENER_DATA_DIR"
ENV_DEFAULT_MODEL = "LISTENER_DEFAULT_MODEL"

DEFAULT_MODEL = "large-v3"
WHISPER_MODELS: list[tuple[str, str]] = [
    ("large-v3", "Large v3: most accurate, slowest (3 GB download)"),
    ("large-v3-turbo", "Large v3 turbo: nearly as accurate, much faster (1.6 GB)"),
    ("medium", "Medium: faster, less accurate (1.5 GB)"),
    ("small", "Small: fastest, rough (500 MB)"),
]
MODEL_IDS = tuple(model_id for model_id, _ in WHISPER_MODELS)

CLAUDE_AUTH_MODES = ("max", "api")

_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load the full config file, returning {} if missing or invalid."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    """Write the full config back (all keys, not just ours)."""
    import yaml
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)


def update_config(**fields) -> dict:
    """Merge fields into the config file. A value of ``None`` removes the key."""
    with _LOCK:
        cfg = load_config()
        for key, value in fields.items():
            if value is None:
                cfg.pop(key, None)
            else:
                cfg[key] = value
        save_config(cfg)
        return cfg


# ---------------------------------------------------------------------------
# Data location
# ---------------------------------------------------------------------------

def resolve_data_dir(default: Path | None = None) -> Path | None:
    """Root folder for meeting data: env var, then config, then ``default``."""
    raw = os.environ.get(ENV_DATA_DIR, "").strip()
    if raw:
        return Path(raw).expanduser()
    configured = str(load_config().get("data_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return default


def transcripts_dir(default_root: Path | None = None) -> Path:
    """The transcripts folder: ``<data_dir>/transcripts`` or ``./transcripts``."""
    root = resolve_data_dir(default_root)
    return root / "transcripts" if root is not None else Path("./transcripts")


# ---------------------------------------------------------------------------
# Whisper model
# ---------------------------------------------------------------------------

def input_device() -> str | None:
    """Preferred microphone, remembered by name (indices shift as devices come and go)."""
    name = str(load_config().get("input_device") or "").strip()
    return name or None


def default_model() -> str:
    """Model preselected in the UI: config, then env, then ``DEFAULT_MODEL``."""
    for candidate in (load_config().get("default_model"), os.environ.get(ENV_DEFAULT_MODEL)):
        candidate = str(candidate or "").strip()
        if candidate in MODEL_IDS:
            return candidate
    return DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Claude access
# ---------------------------------------------------------------------------

def claude_auth_mode() -> str:
    mode = str(load_config().get("claude_auth") or "").strip()
    return mode if mode in CLAUDE_AUTH_MODES else "max"


def anthropic_api_key() -> str | None:
    """Key for API mode: the saved one, else the process environment."""
    saved = str(load_config().get("anthropic_api_key") or "").strip()
    if saved:
        return saved
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return env or None


def claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def claude_status() -> dict:
    """What the UI shows under "Claude access". Never includes the key itself."""
    mode = claude_auth_mode()
    cli = claude_cli_available()
    key = anthropic_api_key()
    hint = f"…{key[-4:]}" if key and len(key) >= 8 else ""
    if mode == "api":
        ready = key is not None
        note = (f"API key saved ({hint}). Analysis, memory and Ask are billed to it."
                if ready else "No API key saved yet. Paste one and press Save.")
    else:
        ready = cli
        note = ("Claude Code login found on this Mac. Analysis, memory and Ask use it."
                if ready else
                "Claude Code is not installed on this Mac. Install it and log in, or switch to an API key.")
    return {"mode": mode, "claude_cli": cli, "api_key_set": key is not None,
            "api_key_hint": hint, "ready": ready, "note": note}


def set_claude_access(mode: str, api_key: str | None = None, *, clear_key: bool = False) -> dict:
    """Persist the access mode and optionally the key. Returns :func:`claude_status`."""
    if mode not in CLAUDE_AUTH_MODES:
        raise ValueError(f"mode must be one of {', '.join(CLAUDE_AUTH_MODES)}")
    fields: dict = {"claude_auth": mode}
    if clear_key:
        fields["anthropic_api_key"] = None
    elif api_key is not None and api_key.strip():
        fields["anthropic_api_key"] = api_key.strip()
    update_config(**fields)
    return claude_status()
