"""Webhook dispatcher for Listener.

Loads webhook configuration from ~/.listener/config.yaml,
formats payloads (JSON or Slack Block Kit), and fires
webhooks asynchronously after session processing completes.
"""

import json
import logging
import threading
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".listener" / "config.yaml"


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load the full config.yaml, returning {} if missing or invalid."""
    from listener import settings
    return settings.load_config()


def _save_config(cfg: dict) -> None:
    """Write the full config back to config.yaml."""
    from listener import settings
    settings.save_config(cfg)


def list_webhooks() -> list[dict]:
    """Return all configured webhooks with their IDs."""
    cfg = _load_config()
    hooks = cfg.get("webhooks", [])
    # Ensure each webhook has an id
    for i, hook in enumerate(hooks):
        if "id" not in hook:
            hook["id"] = str(uuid.uuid4())[:8]
    return hooks


def add_webhook(url: str, events: list[str] | None = None,
                format: str = "json") -> dict:
    """Add a new webhook to the config. Returns the new webhook dict."""
    cfg = _load_config()
    if "webhooks" not in cfg:
        cfg["webhooks"] = []

    webhook = {
        "id": str(uuid.uuid4())[:8],
        "url": url,
        "events": events or ["session_complete"],
        "format": format,  # "json" | "slack" | "markdown"
    }
    cfg["webhooks"].append(webhook)
    _save_config(cfg)
    return webhook


def remove_webhook(webhook_id: str) -> bool:
    """Remove a webhook by ID. Returns True if found and removed."""
    cfg = _load_config()
    hooks = cfg.get("webhooks", [])
    original_len = len(hooks)
    cfg["webhooks"] = [h for h in hooks if h.get("id") != webhook_id]
    if len(cfg["webhooks"]) < original_len:
        _save_config(cfg)
        return True
    return False


# ---------------------------------------------------------------------------
# Payload formatting
# ---------------------------------------------------------------------------

def _build_json_payload(event: str, session_id: str, meta: dict,
                        base_url: str, files: dict) -> dict:
    """Build a standard JSON webhook payload."""
    # Extract action items from analysis if available
    action_items = []
    analysis_text = meta.get("_analysis_text", "")
    if analysis_text:
        # Simple extraction: lines starting with "- [ ]" or "- "
        for line in analysis_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- [ ] "):
                action_items.append(stripped[6:])
            elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
                action_items.append(stripped[6:])

    summary = meta.get("_analysis_text", "")[:500] if meta.get("_analysis_text") else ""

    payload = {
        "event": event,
        "session_id": session_id,
        "title": meta.get("title", f"Meeting {session_id}"),
        "duration": meta.get("duration", 0),
        "language": meta.get("language", ""),
        "summary": summary,
        "action_items": action_items[:10],  # cap at 10
        "files": {},
    }

    if files.get("transcript"):
        payload["files"]["transcript"] = f"{base_url}/api/download/{files['transcript']}"
    if files.get("analysis"):
        payload["files"]["analysis"] = f"{base_url}/api/download/{files['analysis']}"

    return payload


def _build_slack_payload(event: str, session_id: str, meta: dict,
                         base_url: str, files: dict) -> dict:
    """Build a Slack Block Kit payload."""
    title = meta.get("title", f"Meeting {session_id}")
    duration_secs = meta.get("duration", 0)
    dur_m = int(duration_secs // 60)
    dur_s = int(duration_secs % 60)
    duration_str = f"{dur_m}m {dur_s}s" if dur_m else f"{dur_s}s"
    language = (meta.get("language") or "unknown").upper()

    summary = meta.get("_analysis_text", "")[:500] if meta.get("_analysis_text") else "No analysis available."

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📝 {title}", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Duration:* {duration_str}"},
                {"type": "mrkdwn", "text": f"*Language:* {language}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"}
        },
    ]

    # Add file links
    links = []
    if files.get("transcript"):
        links.append(f"<{base_url}/api/download/{files['transcript']}|📄 Transcript>")
    if files.get("analysis"):
        links.append(f"<{base_url}/api/download/{files['analysis']}|📊 Analysis>")

    if links:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Files:* " + "  |  ".join(links)}
        })

    return {"blocks": blocks}


def _build_markdown_payload(event: str, session_id: str, meta: dict,
                            base_url: str, files: dict) -> dict:
    """Build a simple markdown-formatted payload (for generic webhooks)."""
    title = meta.get("title", f"Meeting {session_id}")
    summary = meta.get("_analysis_text", "")[:500] if meta.get("_analysis_text") else ""

    text = f"## {title}\n\n"
    text += f"**Session:** {session_id}\n"
    text += f"**Duration:** {meta.get('duration', 0):.0f}s\n"
    text += f"**Language:** {(meta.get('language') or 'unknown').upper()}\n\n"
    if summary:
        text += f"### Summary\n{summary}\n\n"

    file_links = []
    if files.get("transcript"):
        file_links.append(f"- [Transcript]({base_url}/api/download/{files['transcript']})")
    if files.get("analysis"):
        file_links.append(f"- [Analysis]({base_url}/api/download/{files['analysis']})")
    if file_links:
        text += "### Files\n" + "\n".join(file_links) + "\n"

    return {"text": text}


# ---------------------------------------------------------------------------
# Firing webhooks
# ---------------------------------------------------------------------------

def _send_webhook(url: str, payload: dict, timeout: int = 10) -> tuple[bool, str]:
    """Send a single webhook POST. Returns (success, message)."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Listener/0.1"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URL Error: {e.reason}"
    except Exception as e:
        return False, str(e)


def fire_webhooks(event: str, session_id: str, meta: dict,
                  files: dict, base_url: str = "http://127.0.0.1:8642") -> list[dict]:
    """Fire all configured webhooks for the given event.

    Runs each webhook POST in a separate thread for non-blocking delivery.
    Returns a list of result dicts with {url, success, message}.
    """
    hooks = list_webhooks()
    results = []

    if not hooks:
        return results

    # Filter hooks that subscribe to this event
    matching = [h for h in hooks if event in h.get("events", [])]
    if not matching:
        return results

    logger.info("Firing %d webhook(s) for event '%s' session '%s'",
                len(matching), event, session_id)

    def _fire_one(hook):
        fmt = hook.get("format", "json")
        if fmt == "slack":
            payload = _build_slack_payload(event, session_id, meta, base_url, files)
        elif fmt == "markdown":
            payload = _build_markdown_payload(event, session_id, meta, base_url, files)
        else:
            payload = _build_json_payload(event, session_id, meta, base_url, files)

        success, message = _send_webhook(hook["url"], payload)
        result = {"url": hook["url"], "id": hook.get("id"), "success": success, "message": message}
        if success:
            logger.info("Webhook delivered to %s", hook["url"])
        else:
            logger.warning("Webhook failed for %s: %s", hook["url"], message)
        return result

    # Fire in parallel threads
    thread_results = [None] * len(matching)

    def _run(i, hook):
        thread_results[i] = _fire_one(hook)

    threads = []
    for i, hook in enumerate(matching):
        t = threading.Thread(target=_run, args=(i, hook), daemon=True)
        threads.append(t)
        t.start()

    # Wait for all with a total timeout of 15s
    for t in threads:
        t.join(timeout=15)

    return [r for r in thread_results if r is not None]


def build_test_payload(webhook_id: str, base_url: str = "http://127.0.0.1:8642") -> dict | None:
    """Build and send a test payload for a specific webhook. Returns result dict."""
    hooks = list_webhooks()
    hook = next((h for h in hooks if h.get("id") == webhook_id), None)
    if not hook:
        return None

    test_meta = {
        "title": "Test Meeting - Webhook Verification",
        "duration": 1800.0,
        "language": "en",
        "_analysis_text": "This is a test webhook payload from Listener. "
                          "If you see this message, your webhook integration is working correctly.",
    }
    test_files = {
        "transcript": "test_transcript.md",
        "analysis": "test_analysis.md",
    }

    fmt = hook.get("format", "json")
    if fmt == "slack":
        payload = _build_slack_payload("test", "test-session", test_meta, base_url, test_files)
    elif fmt == "markdown":
        payload = _build_markdown_payload("test", "test-session", test_meta, base_url, test_files)
    else:
        payload = _build_json_payload("test", "test-session", test_meta, base_url, test_files)

    success, message = _send_webhook(hook["url"], payload)
    return {"url": hook["url"], "id": webhook_id, "success": success, "message": message}
