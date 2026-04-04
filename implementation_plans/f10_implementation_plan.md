# Implementation Plan: F10 — Webhooks & API for Automation

## Pre-Implementation Checklist

Before writing any code, the implementor **MUST** read these files fresh (they may have been modified by previous features):

- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (current: 398 lines, has F1 diarization + F8 analytics endpoints)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Single-file frontend (current: 852 lines, has speaker legend + analytics dashboard)
- `/Users/alperencngzz/Desktop/listener/listener/diarizer.py` — Uses `~/.listener/config.yaml` pattern already (YAML config loading exists here)
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Current dependencies

### Things to verify:
- `pyyaml` is NOT in `pyproject.toml` yet (diarizer imports yaml optionally). We need it for webhook config.
- The `~/.listener/config.yaml` pattern already exists in `diarizer.py` — we reuse and extend it.
- `requests` is NOT in dependencies. We'll use `urllib.request` from stdlib (as IMPROVEMENTS.md notes: "urllib.request or requests (already available)").
- `_process_recording()` in `app.py` ends with setting `_state["status"] = "done"` — webhook firing goes right before that.

---

## Dependencies

### Add to pyproject.toml:
```
"pyyaml>=6.0",
```

**Why:** The config.yaml pattern is already used by diarizer.py (it does `import yaml` inline). Adding pyyaml as a formal dependency ensures it's always available for webhook config loading.

**Install command:**
```bash
pip install pyyaml>=6.0
```

No other new dependencies needed. We use `urllib.request` (stdlib) for HTTP calls.

---

## Implementation Tasks

### Task 1: Create `/Users/alperencngzz/Desktop/listener/listener/webhooks.py`

- **File:** `/Users/alperencngzz/Desktop/listener/listener/webhooks.py`
- **Action:** CREATE
- **Details:** This is the core webhook module. It handles config loading/saving, payload formatting, and async webhook firing.

```python
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
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    """Write the full config back to config.yaml."""
    import yaml
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


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
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.webhooks import list_webhooks, add_webhook, fire_webhooks; print('webhooks module OK')"
```

---

### Task 2: Add `pyyaml` dependency to pyproject.toml

- **File:** `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- **Action:** MODIFY
- **Details:** Read the file fresh. Add `"pyyaml>=6.0",` to the `dependencies` list. It should go after the existing dependencies. The current last dependency is `"torch>=2.0.0",`.

Change:
```
    "torch>=2.0.0",
]
```
To:
```
    "torch>=2.0.0",
    "pyyaml>=6.0",
]
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "import yaml; print('pyyaml OK')"
```

---

### Task 3: Add webhook management endpoints to Flask app

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:** Read the file fresh before editing. Add 4 new endpoints and integrate webhook firing into `_process_recording()`.

#### 3a: Add webhook API endpoints

Add the following endpoints **before** the `# Background processing` section (i.e., before the `_process_recording` function). In the current file, that means adding after the `api_analytics` endpoint (around line 233):

```python
# ---------------------------------------------------------------------------
# Webhooks API (F10)
# ---------------------------------------------------------------------------

@app.route("/api/webhooks")
def api_webhooks_list():
    """List all configured webhooks."""
    from listener.webhooks import list_webhooks
    return jsonify(list_webhooks())


@app.route("/api/webhooks", methods=["POST"])
def api_webhooks_add():
    """Add a new webhook."""
    from listener.webhooks import add_webhook
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    events = data.get("events", ["session_complete"])
    fmt = data.get("format", "json")
    if fmt not in ("json", "slack", "markdown"):
        return jsonify({"error": "Format must be json, slack, or markdown"}), 400
    webhook = add_webhook(url, events=events, format=fmt)
    return jsonify(webhook), 201


@app.route("/api/webhooks/<webhook_id>", methods=["DELETE"])
def api_webhooks_delete(webhook_id):
    """Remove a webhook by ID."""
    from listener.webhooks import remove_webhook
    if remove_webhook(webhook_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Webhook not found"}), 404


@app.route("/api/webhooks/test/<webhook_id>", methods=["POST"])
def api_webhooks_test(webhook_id):
    """Send a test payload to a specific webhook."""
    from listener.webhooks import build_test_payload
    result = build_test_payload(webhook_id, base_url=request.host_url.rstrip("/"))
    if result is None:
        return jsonify({"error": "Webhook not found"}), 404
    return jsonify(result)
```

#### 3b: Integrate webhook firing into `_process_recording()`

In the `_process_recording()` function, add webhook firing **after** the analysis is saved but **before** the final `_state["status"] = "done"` block.

Find this block (near the end of `_process_recording`):

```python
        with _lock:
            _state["status"] = "done"
            _state["step"] = None
            _state["files"] = files
            _state["title"] = title
```

Add the following **immediately before** that block:

```python
        # F10: Fire webhooks asynchronously
        try:
            from listener.webhooks import fire_webhooks
            webhook_meta = dict(meta)  # copy meta dict
            # Attach analysis text for summary extraction (not persisted)
            if not skip_analysis and 'analysis_filename' in dir():
                try:
                    analysis_path = OUTPUT_DIR / files.get("analysis", "")
                    if analysis_path.exists():
                        webhook_meta["_analysis_text"] = analysis_path.read_text()[:1000]
                except Exception:
                    pass
            fire_webhooks(
                event="session_complete",
                session_id=session_id,
                meta=webhook_meta,
                files=files,
                base_url="http://127.0.0.1:8642",
            )
        except Exception as e:
            logger.warning("Webhook firing failed: %s", e)
```

**IMPORTANT:** The variable `analysis` already exists in scope at that point (it holds the analysis text). So simplify the analysis text attachment. Actually, let me correct: the `analysis` variable is only defined inside the `if not skip_analysis:` block. So we need a safer approach.

Replace the above with this corrected version — add this right before the `with _lock: _state["status"] = "done"` block:

```python
        # F10: Fire webhooks asynchronously
        try:
            from listener.webhooks import fire_webhooks
            webhook_meta = dict(meta)  # copy meta dict
            # Attach analysis text for summary/action_items extraction
            analysis_file = files.get("analysis")
            if analysis_file:
                try:
                    webhook_meta["_analysis_text"] = (OUTPUT_DIR / analysis_file).read_text()[:1000]
                except Exception:
                    pass
            fire_webhooks(
                event="session_complete",
                session_id=session_id,
                meta=webhook_meta,
                files=files,
                base_url="http://127.0.0.1:8642",
            )
        except Exception as e:
            logger.warning("Webhook firing failed: %s", e)
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('app imports OK')"
```

---

### Task 4: Add webhook management UI to index.html

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:** Read the file fresh before editing. Add a webhooks management card to the UI.

#### 4a: Add CSS (in the `<style>` block, before `</style>`)

Find the closing `</style>` tag and add this CSS **before** it:

```css
/* ---- Webhooks (F10) ---- */
.webhook-list{list-style:none;padding:0;margin:0}
.webhook-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 12px;margin-bottom:6px;background:#f8fafc;
  border-radius:8px;border:1px solid #e2e8f0;gap:8px;
}
.webhook-item .wh-info{flex:1;min-width:0}
.webhook-item .wh-url{font-size:13px;color:#1e293b;font-weight:500;word-break:break-all}
.webhook-item .wh-meta{font-size:11px;color:#94a3b8;margin-top:2px}
.webhook-item .wh-actions{display:flex;gap:4px;flex-shrink:0}
.wh-btn{
  padding:5px 10px;border:1px solid #e2e8f0;border-radius:6px;
  background:#fff;font-size:11px;font-weight:600;cursor:pointer;
  transition:all .15s;color:#475569;
}
.wh-btn:hover{background:#f1f5f9;border-color:#cbd5e1}
.wh-btn.del{color:#ef4444;border-color:#fecaca}
.wh-btn.del:hover{background:#fef2f2;border-color:#ef4444}
.wh-btn.test{color:#6366f1;border-color:#c7d2fe}
.wh-btn.test:hover{background:#eef2ff;border-color:#6366f1}
.wh-form{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.wh-form input[type="url"]{
  flex:1;min-width:200px;padding:7px 10px;border:1px solid #e2e8f0;
  border-radius:8px;font-size:13px;outline:none;
}
.wh-form input[type="url"]:focus{border-color:#6366f1}
.wh-form select{
  padding:7px 10px;border:1px solid #e2e8f0;border-radius:8px;
  font-size:13px;background:#fff;
}
.wh-add-btn{
  padding:7px 16px;background:#6366f1;color:#fff;border:none;
  border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;
  transition:background .15s;
}
.wh-add-btn:hover{background:#4f46e5}
.wh-status{font-size:12px;margin-top:6px;min-height:18px}
.wh-status.ok{color:#16a34a}
.wh-status.fail{color:#ef4444}
.wh-empty{text-align:center;color:#94a3b8;font-size:13px;padding:12px 0}
```

#### 4b: Add HTML (webhook configuration card)

In the HTML body, add a new card **after** the "Past Sessions" card (i.e., before `</div>` that closes `<div class="container">`).

Find this closing sequence:
```html
  </div>
</div>

<script>
```

The first `</div>` closes the sessions card, the second `</div>` closes `.container`. Add the webhooks card **between** the sessions card closing `</div>` and the container closing `</div>`:

```html
  <!-- Webhooks (F10) -->
  <div class="card" id="webhooks-card">
    <div class="card-label">Webhooks</div>
    <ul class="webhook-list" id="wh-list">
      <li class="wh-empty">Loading...</li>
    </ul>
    <div class="wh-form">
      <input type="url" id="wh-url" placeholder="https://hooks.slack.com/..." />
      <select id="wh-format">
        <option value="json">JSON</option>
        <option value="slack">Slack</option>
        <option value="markdown">Markdown</option>
      </select>
      <button class="wh-add-btn" onclick="addWebhook()">Add</button>
    </div>
    <div class="wh-status" id="wh-status"></div>
  </div>
```

#### 4c: Add JavaScript (webhook management functions)

Add the following JavaScript **before** the closing `</script>` tag, after the helpers section:

```javascript
// ===========================================================================
// Webhooks (F10)
// ===========================================================================
async function loadWebhooks(){
  try{
    const r=await f('/api/webhooks');
    const hooks=await r.json();
    const ul=Q('#wh-list');
    if(!hooks.length){
      ul.innerHTML='<li class="wh-empty">No webhooks configured</li>';
      return;
    }
    ul.innerHTML=hooks.map(h=>{
      const evts=h.events?h.events.join(', '):'session_complete';
      return `<li class="webhook-item">
        <div class="wh-info">
          <div class="wh-url">${esc(h.url)}</div>
          <div class="wh-meta">${esc(h.format||'json')} &middot; ${esc(evts)}</div>
        </div>
        <div class="wh-actions">
          <button class="wh-btn test" onclick="testWebhook('${esc(h.id)}')">Test</button>
          <button class="wh-btn del" onclick="deleteWebhook('${esc(h.id)}')">Delete</button>
        </div>
      </li>`;
    }).join('');
  }catch(e){
    Q('#wh-list').innerHTML='<li class="wh-empty">Failed to load webhooks</li>';
  }
}

async function addWebhook(){
  const url=Q('#wh-url').value.trim();
  const fmt=Q('#wh-format').value;
  const st=Q('#wh-status');
  if(!url){st.textContent='Please enter a URL';st.className='wh-status fail';return}
  try{
    const r=await f('/api/webhooks',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:url,format:fmt,events:['session_complete']})
    });
    if(!r.ok){const d=await r.json();st.textContent=d.error||'Failed';st.className='wh-status fail';return}
    Q('#wh-url').value='';
    st.textContent='Webhook added';st.className='wh-status ok';
    setTimeout(()=>{st.textContent=''},3000);
    loadWebhooks();
  }catch(e){st.textContent='Error adding webhook';st.className='wh-status fail'}
}

async function deleteWebhook(id){
  if(!confirm('Remove this webhook?'))return;
  try{
    await f(`/api/webhooks/${id}`,{method:'DELETE'});
    loadWebhooks();
  }catch(e){console.error(e)}
}

async function testWebhook(id){
  const st=Q('#wh-status');
  st.textContent='Sending test...';st.className='wh-status';
  try{
    const r=await f(`/api/webhooks/test/${id}`,{method:'POST'});
    const d=await r.json();
    if(d.success){
      st.textContent='Test delivered: '+d.message;st.className='wh-status ok';
    }else{
      st.textContent='Test failed: '+d.message;st.className='wh-status fail';
    }
    setTimeout(()=>{st.textContent=''},5000);
  }catch(e){st.textContent='Test error';st.className='wh-status fail'}
}
```

#### 4d: Call `loadWebhooks()` on init

Find the init IIFE in the `<script>` block:
```javascript
(async()=>{
  await loadDevices();
  await loadSessions();
  setInterval(poll, 1200);
})();
```

Add `loadWebhooks();` call:
```javascript
(async()=>{
  await loadDevices();
  await loadSessions();
  loadWebhooks();
  setInterval(poll, 1200);
})();
```

Note: `loadWebhooks()` is NOT awaited — it can load in the background.

- **Verification:** Start the web server and verify the webhooks card appears:
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('Template loads OK')"
```

---

## Summary of All File Changes

| File | Action | Description |
|---|---|---|
| `listener/webhooks.py` | **CREATE** | Core webhook module: config CRUD, payload formatting (JSON/Slack/Markdown), async firing |
| `listener/web/app.py` | **MODIFY** | 4 webhook endpoints + webhook trigger in `_process_recording()` |
| `listener/web/templates/index.html` | **MODIFY** | Webhook management UI card with CSS, HTML, and JS |
| `pyproject.toml` | **MODIFY** | Add `pyyaml>=6.0` |

---

## Final Verification

### 1. Module imports:
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "from listener.webhooks import list_webhooks, add_webhook, remove_webhook, fire_webhooks, build_test_payload; print('All webhook functions importable')"
```

### 2. Config CRUD (non-destructive test):
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "
from listener.webhooks import list_webhooks, add_webhook, remove_webhook
# Should be empty or have existing hooks
hooks = list_webhooks()
print(f'Current webhooks: {len(hooks)}')
# Add a test webhook
wh = add_webhook('https://httpbin.org/post', format='json')
print(f'Added webhook: {wh[\"id\"]}')
# List
hooks = list_webhooks()
print(f'After add: {len(hooks)} webhooks')
# Remove
removed = remove_webhook(wh['id'])
print(f'Removed: {removed}')
hooks = list_webhooks()
print(f'After remove: {len(hooks)} webhooks')
print('CRUD test passed')
"
```

### 3. Flask endpoints:
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "
from listener.web.app import app
client = app.test_client()
# List webhooks
r = client.get('/api/webhooks')
assert r.status_code == 200, f'GET /api/webhooks failed: {r.status_code}'
print('GET /api/webhooks OK')
# Add webhook
r = client.post('/api/webhooks', json={'url': 'https://httpbin.org/post', 'format': 'json'})
assert r.status_code == 201, f'POST /api/webhooks failed: {r.status_code}'
wh_id = r.get_json()['id']
print(f'POST /api/webhooks OK (id={wh_id})')
# Delete webhook
r = client.delete(f'/api/webhooks/{wh_id}')
assert r.status_code == 200, f'DELETE /api/webhooks failed: {r.status_code}'
print('DELETE /api/webhooks OK')
print('All endpoint tests passed')
"
```

### 4. Web app loads:
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "
from listener.web.app import app
client = app.test_client()
r = client.get('/')
assert r.status_code == 200
assert b'Webhooks' in r.data, 'Webhooks card not found in HTML'
assert b'wh-url' in r.data, 'Webhook URL input not found'
print('Frontend loads with webhook UI OK')
"
```

---

## Notes for the Implementor

1. **The `~/.listener/config.yaml` file already exists in the project** — `diarizer.py` reads `hf_token` from it. Our webhook config adds a `webhooks` key at the same level. The `_save_config()` function writes back the full dict, preserving existing keys like `hf_token`.

2. **No database needed** — webhook config is file-based (YAML), consistent with the project's file-based philosophy.

3. **Webhooks fire synchronously in `_process_recording()`** but each HTTP POST runs in its own thread with a 15s timeout. This means the "done" state is delayed by at most 15s — acceptable for a local tool.

4. **The `_analysis_text` key** in `webhook_meta` is a transient key used only for payload building (extracting summary and action items). It's not persisted to `meta.json`.

5. **Event types:** Currently only `session_complete` is supported. The schema allows for future events (e.g., `recording_started`, `transcription_complete`) by extending the events list.

6. **Slack format** uses Block Kit JSON, which is what Slack incoming webhooks expect.

7. **Security note:** Webhook URLs may contain secrets (e.g., Slack tokens). They're stored in `~/.listener/config.yaml` which has standard user-only permissions. The UI displays full URLs — this is acceptable for a localhost-only tool.
