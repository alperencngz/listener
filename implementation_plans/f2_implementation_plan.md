# Implementation Plan: F2 — Chat with Transcript

## Pre-Implementation Checklist

**Files the implementor MUST read fresh before starting** (these are shared files modified by F1, F8, F9, F10):
- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (has F1, F8, F9, F10 additions)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Frontend (has F1, F8, F9, F10 additions)
- `/Users/alperencngzz/Desktop/listener/listener/claude/runner.py` — Claude SDK runner (the `run_claude_session` function signature)
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Current dependencies

**Things to verify:**
- `listener/chat.py` does NOT already exist (this is a new file)
- `run_claude_session` in runner.py accepts: `prompt`, `system_prompt`, `model`, `node_name` keyword args
- The `renderMd()` function exists in index.html's `<script>` section (it will be reused for chat message rendering)
- The `OUTPUT_DIR` variable is defined at top of app.py as `Path("./transcripts")`

## Dependencies

**No new dependencies needed.** F2 uses the existing `claude-code-sdk` already in pyproject.toml. All chat logic runs through `listener/claude/runner.py`.

## Implementation Tasks

### Task 1: Create `listener/chat.py`

- **File:** `/Users/alperencngzz/Desktop/listener/listener/chat.py`
- **Action:** CREATE
- **Details:** Create the chat module with `ChatSession` class. This is the core backend logic.

```python
import asyncio
from dataclasses import dataclass, field
from listener.claude.runner import run_claude_session

CHAT_SYSTEM_PROMPT = """\
You are a meeting assistant. You answer questions about a specific meeting \
based ONLY on the transcript provided.

Rules:
- Answer in the same language the user asks in.
- Reference specific timestamps [HH:MM:SS] when citing the transcript.
- If the answer is not in the transcript, say so explicitly.
- Be concise but thorough.
- For follow-up questions, use the conversation history for context."""


@dataclass
class ChatSession:
    transcript: str
    history: list[dict] = field(default_factory=list)
    max_history_turns: int = 20

    def build_prompt(self, user_message: str) -> str:
        parts = [f"## Meeting Transcript\n\n{self.transcript}\n\n---\n"]
        if self.history:
            parts.append("## Conversation History\n")
            for msg in self.history[-self.max_history_turns:]:
                role = "User" if msg["role"] == "user" else "Assistant"
                parts.append(f"**{role}:** {msg['content']}\n")
            parts.append("---\n")
        parts.append(f"## Current Question\n\n{user_message}")
        return "\n".join(parts)

    async def ask(self, user_message: str, model: str = "claude-sonnet-4-5") -> str:
        prompt = self.build_prompt(user_message)
        response = await run_claude_session(
            prompt=prompt,
            system_prompt=CHAT_SYSTEM_PROMPT,
            model=model,
            node_name="chat_with_transcript",
        )
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": response})
        return response

    def ask_sync(self, user_message: str, model: str = "claude-sonnet-4-5") -> str:
        return asyncio.run(self.ask(user_message, model=model))
```

- **Verification:** `python -c "from listener.chat import ChatSession; print('OK')"`

### Task 2: Add Chat Endpoints to Flask app.py

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:** Add 3 new endpoints and an import/dict for chat sessions. READ THE FILE FRESH before editing.

**Step 2a:** Add this line near the top of the file, after the existing imports but before `app = Flask(__name__)`:
```python
from listener.chat import ChatSession
```

NOTE: If the import causes circular dependency issues, use a lazy import inside the endpoint functions instead (like the existing diarizer imports).

**Step 2b:** Add session storage dict after the existing `_streamer = None` line (around line 36):
```python
# In-memory chat sessions, keyed by session_id
_chat_sessions: dict[str, ChatSession] = {}
```

**Step 2c:** Add 3 new endpoint functions. Place them AFTER the existing `/api/analytics/<session_id>` endpoint and BEFORE the `# Webhooks API` section comment. Here is the exact code to add:

```python
# ---------------------------------------------------------------------------
# Chat with Transcript (F2)
# ---------------------------------------------------------------------------

@app.route("/api/chat/<session_id>", methods=["POST"])
def api_chat(session_id):
    """Send a chat message about a transcript and get AI response."""
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    transcript_file = OUTPUT_DIR / f"{session_id}_transcript.md"
    if not transcript_file.exists():
        return jsonify({"error": "Transcript not found"}), 404
    transcript = transcript_file.read_text()
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = ChatSession(transcript=transcript)
    chat = _chat_sessions[session_id]
    try:
        response = chat.ask_sync(message)
    except Exception as e:
        logger.error("Chat error for session %s: %s", session_id, e)
        return jsonify({"error": f"Chat failed: {str(e)}"}), 500
    return jsonify({"response": response, "history_length": len(chat.history)})


@app.route("/api/chat/<session_id>/history")
def api_chat_history(session_id):
    """Get chat history for a session."""
    chat = _chat_sessions.get(session_id)
    return jsonify({"history": chat.history if chat else []})


@app.route("/api/chat/<session_id>/clear", methods=["POST"])
def api_chat_clear(session_id):
    """Clear chat history for a session."""
    _chat_sessions.pop(session_id, None)
    return jsonify({"ok": True})
```

- **Verification:** `python -c "from listener.web.app import app; rules=[r.rule for r in app.url_map.iter_rules() if 'chat' in r.rule]; print(rules); assert len(rules)==3"`

### Task 3: Add Chat Tab to Frontend — CSS

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:** READ THE FILE FRESH. Add CSS rules inside the existing `<style>` block, right before the closing `</style>` tag (currently before the `<script src="https://cdn.jsdelivr.net/npm/chart.js...` line).

Add this CSS:

```css
/* ---- Chat with Transcript (F2) ---- */
.chat-container{
  display:flex;flex-direction:column;height:420px;
  background:#f8fafc;border-radius:0 0 10px 10px;
}
.chat-messages{
  flex:1;overflow-y:auto;padding:16px 20px;
  display:flex;flex-direction:column;gap:12px;
}
.chat-messages::-webkit-scrollbar{width:5px}
.chat-messages::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
.chat-msg{
  max-width:85%;padding:10px 14px;border-radius:12px;
  font-size:13px;line-height:1.6;word-wrap:break-word;
}
.chat-msg.user{
  align-self:flex-end;background:#6366f1;color:#fff;
  border-bottom-right-radius:4px;
}
.chat-msg.assistant{
  align-self:flex-start;background:#fff;color:#334155;
  border:1px solid #e2e8f0;border-bottom-left-radius:4px;
}
.chat-msg.assistant h1,.chat-msg.assistant h2,.chat-msg.assistant h3{margin-top:8px}
.chat-msg.assistant p{margin:4px 0}
.chat-msg.assistant ul,.chat-msg.assistant ol{padding-left:18px;margin:4px 0}
.chat-msg.assistant .ts{
  font-family:'SF Mono',SFMono-Regular,Menlo,monospace;
  font-size:11px;color:#6366f1;background:#eef2ff;
  padding:1px 4px;border-radius:3px;
}
.chat-msg.error{
  align-self:center;background:#fef2f2;color:#ef4444;
  border:1px solid #fecaca;font-size:12px;
}
.chat-msg.thinking{
  align-self:flex-start;background:#f1f5f9;color:#94a3b8;
  border:1px solid #e2e8f0;font-style:italic;font-size:12px;
}
.chat-empty{
  flex:1;display:flex;align-items:center;justify-content:center;
  color:#94a3b8;font-size:13px;text-align:center;padding:20px;
}
.chat-input-row{
  display:flex;gap:8px;padding:12px 16px;
  border-top:1px solid #e2e8f0;background:#fff;
}
.chat-input{
  flex:1;padding:9px 14px;border:1px solid #e2e8f0;border-radius:10px;
  font-size:13px;outline:none;resize:none;font-family:inherit;
  min-height:38px;max-height:100px;
}
.chat-input:focus{border-color:#6366f1}
.chat-input:disabled{opacity:.5;cursor:not-allowed}
.chat-send{
  padding:9px 18px;background:#6366f1;color:#fff;border:none;
  border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;
  transition:background .15s;white-space:nowrap;
}
.chat-send:hover{background:#4f46e5}
.chat-send:disabled{background:#94a3b8;cursor:not-allowed}
.chat-actions{
  display:flex;justify-content:flex-end;padding:4px 16px 8px;
  background:#fff;border-radius:0 0 10px 10px;
}
.chat-clear{
  padding:4px 10px;border:1px solid #e2e8f0;border-radius:6px;
  background:#fff;font-size:11px;font-weight:600;color:#94a3b8;
  cursor:pointer;transition:all .15s;
}
.chat-clear:hover{color:#ef4444;border-color:#fecaca;background:#fef2f2}
```

### Task 4: Add Chat Tab to Frontend — JS Changes

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:** Four changes needed in the JavaScript section. READ THE FILE FRESH.

**Step 4a: Add the "Chat" tab button in the `openViewer()` function.**

Find the line in `openViewer()` that adds the Analytics tab:
```javascript
  tabs+=`<button class="tab" onclick="switchTab(this,'analytics')">Analytics</button>`;
```

Add the Chat tab button BEFORE that line. The modified section should read:
```javascript
  if(files.transcript) tabs+=`<button class="tab" onclick="switchTab(this,'chat')">Chat</button>`;
  tabs+=`<button class="tab" onclick="switchTab(this,'analytics')">Analytics</button>`;
```

**Step 4b: Handle the 'chat' tab in `loadTab()` function.**

Find this block at the top of `loadTab()`:
```javascript
  if(tab==='analytics'){
    await loadAnalytics(viewerSession);
    return;
  }
```

Add the chat tab handler BEFORE the analytics handler:
```javascript
  if(tab==='chat'){
    loadChatTab(viewerSession);
    return;
  }
```

**Step 4c: Modify `switchTab()` to reset content styles when leaving Chat tab.**

Find the existing `switchTab` function:
```javascript
function switchTab(btn,tab){
  Q('#tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  btn.classList.add('on');
  loadTab(tab);
}
```

Replace it with:
```javascript
function switchTab(btn,tab){
  Q('#tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  btn.classList.add('on');
  // Reset content styles that chat tab may have overridden
  const c = Q('#content');
  if (tab !== 'chat') {
    c.className = 'content';
    c.style.cssText = '';
  }
  loadTab(tab);
}
```

**Step 4d: Add the complete chat JavaScript section.**

Add this entire block BEFORE the `// Sessions list` comment section (find the line `// ===========================================================================` followed by `// Sessions list`). Place it after the Live Transcription section's `closeLiveStream` function.

```javascript
// ===========================================================================
// Chat with Transcript (F2)
// ===========================================================================
let _chatLoading = false;

function loadChatTab(sess) {
  const c = Q('#content');
  if (!sess?.id || !sess.files?.transcript) {
    c.innerHTML = '<p class="chat-empty">No transcript available for chat</p>';
    return;
  }

  c.className = '';
  c.style.cssText = 'max-height:none;overflow:visible;padding:0;background:transparent;border-radius:0 0 10px 10px;';
  c.innerHTML = `
    <div class="chat-container">
      <div class="chat-messages" id="chat-msgs">
        <div class="chat-empty">Ask anything about this meeting transcript.<br>
        <span style="font-size:12px;color:#b0b8c4;margin-top:6px;display:block">
          Try: "What were the main decisions?" or "Summarize the action items"
        </span></div>
      </div>
      <div class="chat-input-row">
        <textarea class="chat-input" id="chat-input" placeholder="Ask about this meeting..." rows="1"
          onkeydown="chatKeydown(event)"></textarea>
        <button class="chat-send" id="chat-send-btn" onclick="sendChatMessage()">Send</button>
      </div>
      <div class="chat-actions">
        <button class="chat-clear" onclick="clearChat()">Clear chat</button>
      </div>
    </div>`;

  // Load existing history
  loadChatHistory(sess.id);

  // Auto-resize textarea
  const ta = Q('#chat-input');
  ta.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  });
}

async function loadChatHistory(sessionId) {
  try {
    const r = await f(`/api/chat/${sessionId}/history`);
    const data = await r.json();
    const hist = data.history || [];
    if (hist.length > 0) {
      const msgs = Q('#chat-msgs');
      msgs.innerHTML = '';
      hist.forEach(msg => {
        appendChatMessage(msg.role, msg.content);
      });
      scrollChatToBottom();
    }
  } catch (e) {
    console.warn('Failed to load chat history:', e);
  }
}

function chatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
}

async function sendChatMessage() {
  if (_chatLoading) return;
  const input = Q('#chat-input');
  const btn = Q('#chat-send-btn');
  const message = input.value.trim();
  if (!message || !viewerSession?.id) return;

  // Clear placeholder if first message
  const msgs = Q('#chat-msgs');
  const placeholder = msgs.querySelector('.chat-empty');
  if (placeholder) placeholder.remove();

  // Show user message
  appendChatMessage('user', message);
  input.value = '';
  input.style.height = 'auto';
  scrollChatToBottom();

  // Show thinking indicator
  const thinkingEl = document.createElement('div');
  thinkingEl.className = 'chat-msg thinking';
  thinkingEl.textContent = 'Thinking...';
  msgs.appendChild(thinkingEl);
  scrollChatToBottom();

  // Disable input
  _chatLoading = true;
  input.disabled = true;
  btn.disabled = true;

  try {
    const r = await f(`/api/chat/${viewerSession.id}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: message})
    });
    thinkingEl.remove();

    if (!r.ok) {
      const err = await r.json();
      appendChatMessage('error', err.error || 'Something went wrong');
    } else {
      const data = await r.json();
      appendChatMessage('assistant', data.response);
    }
  } catch (e) {
    thinkingEl.remove();
    appendChatMessage('error', 'Network error. Please try again.');
  } finally {
    _chatLoading = false;
    input.disabled = false;
    btn.disabled = false;
    input.focus();
    scrollChatToBottom();
  }
}

function appendChatMessage(role, content) {
  const msgs = Q('#chat-msgs');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  if (role === 'assistant') {
    div.innerHTML = renderMd(content);
  } else if (role === 'error') {
    div.textContent = content;
  } else {
    div.textContent = content;
  }
  msgs.appendChild(div);
}

function scrollChatToBottom() {
  const msgs = Q('#chat-msgs');
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

async function clearChat() {
  if (!viewerSession?.id) return;
  try {
    await f(`/api/chat/${viewerSession.id}/clear`, {method: 'POST'});
  } catch (e) {}
  const msgs = Q('#chat-msgs');
  if (msgs) {
    msgs.innerHTML = `<div class="chat-empty">Ask anything about this meeting transcript.<br>
      <span style="font-size:12px;color:#b0b8c4;margin-top:6px;display:block">
        Try: "What were the main decisions?" or "Summarize the action items"
      </span></div>`;
  }
}
```

## Frontend Changes Summary

All changes go to `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`:

1. **CSS** (Task 3): ~80 lines of chat-specific styles added before `</style>`
2. **Tab button** (Task 4a): One line added in `openViewer()` JS function
3. **Tab handler** (Task 4b): 4 lines added in `loadTab()` JS function
4. **switchTab fix** (Task 4c): Modified `switchTab()` to reset styles when leaving chat tab
5. **Chat JS functions** (Task 4d): ~140 lines of chat UI logic added as new section

## Flask Endpoint Changes Summary

All added to `/Users/alperencngzz/Desktop/listener/listener/web/app.py`:

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/chat/<session_id>` | POST | Send message, get AI response |
| `/api/chat/<session_id>/history` | GET | Get conversation history |
| `/api/chat/<session_id>/clear` | POST | Clear conversation history |

## Architecture Notes

- **No RAG needed:** Average 2-hour meeting is ~30K tokens. Claude Sonnet's 200K context window fits this easily. Full-context injection preserves cross-reference ability.
- **In-memory sessions:** `_chat_sessions` dict stores `ChatSession` objects. These are lost on server restart (acceptable for a local tool). Each session is keyed by session_id.
- **Blocking call:** `ask_sync()` uses `asyncio.run()` which blocks the Flask thread. This is fine for a single-user local tool. For multi-user, you'd need async Flask (out of scope).
- **Markdown reuse:** Assistant messages are rendered using the same `renderMd()` function used for transcript/analysis display, so timestamps and formatting are consistent.

## Final Verification

1. **Import check:**
   ```bash
   cd /Users/alperencngzz/Desktop/listener && python -c "from listener.chat import ChatSession; print('chat.py OK')"
   ```

2. **Endpoint check:**
   ```bash
   cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; rules=[r.rule for r in app.url_map.iter_rules() if 'chat' in r.rule]; print(rules); assert len(rules)==3"
   ```

3. **Web app starts:**
   ```bash
   cd /Users/alperencngzz/Desktop/listener && timeout 5 python -c "from listener.web.app import app; print('App loads OK')" 2>&1
   ```

4. **Manual browser test:**
   - Start web server: `cd /Users/alperencngzz/Desktop/listener && listener web`
   - Open http://127.0.0.1:8642
   - Click on any existing session that has a transcript
   - Verify "Chat" tab appears in the tab bar (between Audio and Analytics)
   - Click the Chat tab -- should show the chat UI with placeholder text
   - Type a question and press Enter or click Send
   - Verify the thinking indicator appears, then the AI response renders with markdown
   - Click "Clear chat" to verify history clearing works
   - Switch to another tab and back to verify style reset works
