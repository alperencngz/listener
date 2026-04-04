# Listener — Improvement Roadmap & Feature Specifications

**Generated:** 2026-04-04
**Current Version:** 0.1.0
**Stack:** Python 3.12, Flask, faster-whisper (large-v3), Claude Code SDK (Max OAuth), sounddevice

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Competitive Landscape](#2-competitive-landscape)
3. [Priority Matrix](#3-priority-matrix)
4. [Feature Specifications](#4-feature-specifications)
   - [F1: Speaker Diarization](#f1-speaker-diarization)
   - [F2: Chat with Transcript](#f2-chat-with-transcript)
   - [F3: Custom Analysis Recipes](#f3-custom-analysis-recipes)
   - [F4: Full-Text Search Across Meetings](#f4-full-text-search-across-meetings)
   - [F5: Click-to-Seek Audio Linkage](#f5-click-to-seek-audio-linkage)
   - [F6: Multi-Format Export](#f6-multi-format-export)
   - [F7: Noise Preprocessing](#f7-noise-preprocessing)
   - [F8: Meeting Analytics Dashboard](#f8-meeting-analytics-dashboard)
   - [F9: Real-Time Live Transcription](#f9-real-time-live-transcription)
   - [F10: Webhooks & API for Automation](#f10-webhooks--api-for-automation)
5. [Dependency Summary](#5-dependency-summary)
6. [Architecture Notes](#6-architecture-notes)
7. [Sources & References](#7-sources--references)

---

## 1. Current State Assessment

### What Listener Already Has

| Capability | Status | Notes |
|---|---|---|
| Local-first audio recording | Done | sounddevice, WAV output, single device |
| Speech-to-text (EN + TR) | Done | faster-whisper large-v3, auto language detection |
| Mixed-language support | Done | `condition_on_previous_text=False` for code-switching |
| AI analysis (summary, actions, decisions) | Done | Claude Code SDK, Max OAuth, sonnet-4-5 |
| Title generation | Done | Separate Claude call, saved in meta.json |
| Web UI | Done | Flask on port 8642, inline viewer with tabs |
| Session history | Done | File-based, shows title/date/duration/language |
| Markdown output | Done | Timestamped transcript + analysis files |
| Bot-free recording | Done | No meeting bot, captures system/mic audio |
| CLI interface | Done | `listener record`, `listener transcribe`, `listener analyze`, `listener web` |

### Biggest Gaps vs Competitors

1. **No speaker diarization** — every single competitor (Otter, Fireflies, Fathom, tl;dv, MeetGeek, Tactiq) and every major open-source tool (WhisperX, Meetily, whisper-diarization, Scriberr) has this
2. **No cross-meeting search** — once users accumulate 20+ meetings, finding specific content becomes painful
3. **No conversational Q&A** — cannot ask "what did we decide about X?" like Otter's OtterChat, Fireflies' AskFred, or Scriberr's chat
4. **No audio-text linkage** — cannot click a transcript line to jump to that moment in the audio
5. **Single analysis template** — no way to reprocess meetings through different lenses (sales call, interview, retro)
6. **Single export format** — only .md, no PDF/DOCX/SRT/JSON
7. **No noise preprocessing** — noisy environments degrade Whisper accuracy

### Strengths to Preserve

- **100% local processing** — privacy-first, no cloud dependency for transcription (matches Krisp, Granola philosophy)
- **Claude Max integration** — zero API cost via OAuth, already battle-tested SDK wrapper from AbsolutePath
- **Simplicity** — single `listener web` command, clean UI, no complex setup
- **Multilingual** — English + Turkish code-switching handled well

---

## 2. Competitive Landscape

### Commercial Tools

| Tool | Differentiator | Key Feature to Learn From |
|---|---|---|
| **Otter.ai** | Cross-meeting knowledge base | "OtterChat" — query across all meetings ("What did Sarah say about Q3 budget in last week's meeting?") |
| **Fireflies.ai** | Deepest CRM integration, 100+ languages | "Soundbites" — clip short audio segments and share them; "AI Skills" — custom extractors per team role |
| **Granola.ai** | Bot-free, Notion-style editing | "Recipes" — reusable prompt templates as analytical lenses (29 built-in); MCP server for AI tool chains |
| **tl;dv** | Generous free tier, 30+ languages | Highlight-to-clip — select transcript text to auto-generate a video snippet |
| **Fathom** | Fastest processing (<1 min), genuinely free core | In-meeting moment tagging — click to mark key moments during the call; customizable summary templates |
| **Krisp** | Noise cancellation + transcription | Virtual audio device layer with 40+ dB noise reduction; all processing local/on-device |
| **Tactiq** | Lightest weight (Chrome extension only) | Real-time in-meeting tagging with custom categories (action item, decision, question, highlight) |
| **MeetGeek** | AI Voice Agents, analytics | Auto-detects meeting type and applies correct summary template; talk-time distribution and sentiment analysis; REST API + webhooks |

### Open-Source Repos

| Repo | Stars | Differentiator | Key Technical Approach |
|---|---|---|---|
| **WhisperX** (m-bain/whisperX) | 21.1k | Word-level timestamps + diarization | wav2vec2 forced alignment, pyannote-audio diarization, 70x realtime batched inference |
| **Meetily** (Zackriya-Solutions/meetily) | 10.9k | Full meeting assistant, Tauri desktop app | Rust backend + Next.js frontend, simultaneous mic + system audio, Apple Metal/CoreML |
| **whisper-diarization** (MahmoudAshraf97) | 5.5k | Most sophisticated diarization pipeline | Demucs vocal isolation -> Whisper -> ctc-forced-aligner -> MarbleNet VAD -> TitaNet speaker embeddings -> punctuation realignment |
| **WhisperLive** (collabora/WhisperLive) | 3.9k | True real-time streaming | WebSocket client-server, three backends (faster-whisper, TensorRT, OpenVINO), browser extensions |
| **Scriberr** (rishikanthc/Scriberr) | 2.5k | Chat with audio, seek-from-text | Ollama/OpenAI-compatible LLM chat, waveform visualization, PWA, click transcript to jump to audio position |

### Full Feature Comparison

| Feature | Listener | Otter | Fireflies | Granola | Fathom | Krisp | WhisperX | Meetily | Scriberr |
|---|---|---|---|---|---|---|---|---|---|
| Basic transcription | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Speaker diarization | **No** | Yes | Yes | Partial | Yes | Limited | Yes | Yes | Yes |
| AI summary + actions | Yes | Yes | Yes | Yes | Yes | Yes | No | Yes | Yes |
| Cross-meeting search | **No** | Yes | Yes | No | Yes | No | No | No | No |
| Chat with transcript | **No** | Yes | Yes | Yes | Yes | No | No | No | Yes |
| Custom AI prompts/recipes | **No** | No | Yes | Yes | No | No | No | No | No |
| Click-to-seek | **No** | No | No | No | No | No | No | No | Yes |
| Multi-format export | **No** | Yes | Yes | No | Yes | No | SRT/JSON | PDF/DOCX | No |
| Noise preprocessing | **No** | No | No | No | No | Yes | No | No | No |
| 100% local processing | Yes | No | No | Partial | No | Yes | Yes | Yes | Yes |
| Bot-free recording | Yes | Partial | No | Yes | No | Yes | N/A | Yes | N/A |
| Meeting analytics | **No** | No | Yes | No | No | No | No | No | No |
| Real-time transcription | **No** | Yes | Yes | No | Yes | Yes | No | Yes | No |
| Word-level timestamps | **No** | No | No | No | No | No | Yes | No | Yes |
| API/Webhooks | **No** | No | Yes | MCP | No | No | N/A | No | Yes |

---

## 3. Priority Matrix

| Priority | Feature | User Impact | Implementation Effort | Dependencies |
|---|---|---|---|---|
| **P0** | F1: Speaker Diarization | Critical | Medium (2-3 days) | pyannote-audio, HuggingFace token |
| **P1** | F2: Chat with Transcript | High | Low (1-2 days) | Already have Claude SDK |
| **P1** | F3: Custom Analysis Recipes | High | Low (1 day) | pyyaml |
| **P1** | F5: Click-to-Seek | Medium-High | Low (1 day) | JS only, no new deps |
| **P2** | F4: Full-Text Search | High | Medium (2 days) | sqlite3 (stdlib) |
| **P2** | F6: Multi-Format Export | Medium-High | Medium (2 days) | python-docx, fpdf2 |
| **P2** | F7: Noise Preprocessing | Medium | Low (1 day) | noisereduce |
| **P3** | F8: Meeting Analytics | Medium | Medium (2-3 days) | Requires F1 first |
| **P3** | F10: Webhooks/API | Medium | Low (1 day) | None |
| **P4** | F9: Real-Time Transcription | High | High (3-5 days) | Architecture rethink |

**Recommended implementation order:** F1 -> F2 + F3 + F5 (parallel) -> F4 -> F7 -> F6 -> F8 -> F10 -> F9

---

## 4. Feature Specifications

---

### F1: Speaker Diarization

**Priority:** P0 (Critical)
**Effort:** Medium (2-3 days)
**Why:** Every competitor has this. Without it, transcripts of multi-person meetings are a wall of undifferentiated text. This is the single most requested feature in meeting transcription tools.

#### Approach Options

There are three viable approaches, ranked by quality:

**Option A: pyannote-audio 3.1 + temporal overlap alignment (Recommended)**

Best balance of quality, simplicity, and maintenance.

```
pip install "pyannote.audio>=3.1"
```

- HuggingFace model: `pyannote/speaker-diarization-3.1`
- Also requires accepting license for: `pyannote/segmentation-3.0`
- HuggingFace token required (read access). User must:
  1. Create account at huggingface.co
  2. Generate read token at huggingface.co/settings/tokens
  3. Accept conditions at huggingface.co/pyannote/speaker-diarization-3.1
  4. Accept conditions at huggingface.co/pyannote/segmentation-3.0

**Option B: WhisperX all-in-one pipeline**

Simpler API but replaces our faster-whisper setup entirely.

```
pip install whisperx
```

Handles transcription + word-level alignment + diarization in one API. Still requires HuggingFace token for pyannote internally.

**Option C: simple_diarizer (no HuggingFace token)**

Lower accuracy, but zero gated model access required.

```
pip install simple-diarizer
```

Uses SpeechBrain ECAPA-TDNN embeddings (public models, auto-downloaded). No license agreements needed.

#### Implementation Details (Option A — Recommended)

**Python API:**

```python
import torch
from pyannote.audio import Pipeline
from pyannote.audio.pipelines.utils.hook import ProgressHook

# Initialize (do once, cache the pipeline)
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    use_auth_token="HF_TOKEN",
)
# On M4 16GB: use CPU for reliability. MPS works but uses ~12GB unified memory.
pipeline.to(torch.device("cpu"))

# Run diarization
with ProgressHook() as hook:
    diarization = pipeline("audio.wav", hook=hook)
# Optional: constrain speaker count
# diarization = pipeline("audio.wav", min_speakers=2, max_speakers=6)

# Iterate results
for turn, _, speaker in diarization.itertracks(yield_label=True):
    print(f"{speaker}: {turn.start:.1f}s - {turn.end:.1f}s")
    # turn.start -> float (seconds)
    # turn.end   -> float (seconds)
    # speaker    -> str, e.g. "SPEAKER_00", "SPEAKER_01"
```

**Output format:** `pyannote.core.Annotation` object with `.itertracks(yield_label=True)` yielding `(Segment, track_name, label)` tuples. Segment has `.start`, `.end`, `.duration` as floats in seconds. Label is a string like `"SPEAKER_00"`.

**Alignment algorithm** — for each Whisper segment, find the pyannote speaker with the most temporal overlap:

```python
def align_diarization_with_transcript(diarization, whisper_segments):
    """
    diarization: pyannote Annotation object
    whisper_segments: list of Segment(start, end, text)
    Returns: list with added 'speaker' field
    """
    result = []
    for seg in whisper_segments:
        speaker_durations = {}
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            overlap_start = max(seg.start, turn.start)
            overlap_end = min(seg.end, turn.end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > 0:
                speaker_durations[speaker] = speaker_durations.get(speaker, 0) + overlap

        best_speaker = max(speaker_durations, key=speaker_durations.get) if speaker_durations else "UNKNOWN"
        result.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "speaker": best_speaker,
        })
    return result
```

**Performance on M4 (16GB):**
- CPU mode: 2-4 minutes for a 1-hour file, 3-6 GB RAM
- MPS (Metal GPU): faster but uses ~12 GB unified memory — risky on 16GB total
- Recommendation: default to CPU, add `--device mps` flag for users who want to try GPU

#### Files to Create/Modify

| File | Action | Description |
|---|---|---|
| `listener/diarizer.py` | **Create** | New module: load pyannote pipeline, run diarization, align with Whisper segments |
| `listener/transcriber.py` | **Modify** | Add `speaker` field to `Segment` dataclass. Update `TranscriptionResult` to include speaker info. Modify `to_timestamped_text()` to show speaker labels |
| `listener/web/app.py` | **Modify** | Call diarizer in `_process_recording()` after transcription. Add `"diarizing"` step to state |
| `listener/cli.py` | **Modify** | Add `--hf-token` option and `--no-diarize` flag |
| `listener/web/templates/index.html` | **Modify** | Color-code speakers in transcript view. Add speaker legend |
| `pyproject.toml` | **Modify** | Add `pyannote.audio>=3.1` dependency |

#### Transcript Output Format (with diarization)

**Markdown:**
```markdown
**[00:00:15] Speaker 1:** Hello everyone, welcome to the meeting.

**[00:00:22] Speaker 2:** Thanks for having us. Let's start with the agenda.

**[00:00:35] Speaker 1:** Sure. First item is the Q3 budget review.
```

**JSON (for structured export):**
```json
{
  "segments": [
    {"start": 0.15, "end": 3.5, "text": "Hello everyone, welcome to the meeting.", "speaker": "SPEAKER_00"},
    {"start": 3.8, "end": 7.2, "text": "Thanks for having us. Let's start with the agenda.", "speaker": "SPEAKER_01"}
  ],
  "speakers": {
    "SPEAKER_00": {"label": "Speaker 1", "talk_time_seconds": 1245.3},
    "SPEAKER_01": {"label": "Speaker 2", "talk_time_seconds": 890.7}
  }
}
```

#### Configuration

Store HuggingFace token in environment variable or config file:
- Environment: `HF_TOKEN` or `HUGGINGFACE_TOKEN`
- Config file: `~/.listener/config.yaml` with `hf_token: hf_xxx`
- CLI: `listener record --hf-token hf_xxx`

---

### F2: Chat with Transcript

**Priority:** P1 (High)
**Effort:** Low (1-2 days)
**Why:** Otter (OtterChat), Fireflies (AskFred), Fathom, Granola, and Scriberr all have conversational Q&A over meeting content. Users want to ask "What did we decide about the timeline?" instead of reading 30 minutes of transcript.

#### Architecture Decision: Full Context vs RAG

**Use full-context injection (no RAG needed).** Reasoning:

- Average speech: 130-150 words/minute
- 2-hour meeting: ~16,000-18,000 words -> ~27,000-31,000 tokens with timestamps
- Claude Sonnet context window: 200K tokens — fits trivially
- Full context preserves cross-reference ability ("what did they say *after* the budget discussion?")
- RAG adds complexity (embedding model, vector store, chunk boundary issues) for no benefit at this scale
- RAG would only be needed for cross-meeting search across hundreds of meetings (handled separately by F4)

#### Implementation

**New file: `listener/chat.py`**

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

**Flask endpoints to add to `app.py`:**

```python
# In-memory chat sessions, keyed by session_id
_chat_sessions: dict[str, ChatSession] = {}

@app.route("/api/chat/<session_id>", methods=["POST"])
def api_chat(session_id):
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
    response = chat.ask_sync(message)
    return jsonify({"response": response, "history_length": len(chat.history)})

@app.route("/api/chat/<session_id>/history")
def api_chat_history(session_id):
    chat = _chat_sessions.get(session_id)
    return jsonify({"history": chat.history if chat else []})

@app.route("/api/chat/<session_id>/clear", methods=["POST"])
def api_chat_clear(session_id):
    _chat_sessions.pop(session_id, None)
    return jsonify({"ok": True})
```

**UI component:** Add a "Chat" tab in the session viewer alongside Transcript/Analysis/Audio. The chat tab has:
- Chat message history (scrollable)
- Input field at the bottom with send button
- "Clear chat" button
- Messages rendered with the same markdown renderer used for analysis

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/chat.py` | **Create** |
| `listener/web/app.py` | **Modify** — add 3 chat endpoints |
| `listener/web/templates/index.html` | **Modify** — add Chat tab with message UI |

---

### F3: Custom Analysis Recipes

**Priority:** P1 (High)
**Effort:** Low (1 day)
**Why:** Granola's most praised feature (launched Sept 2025, 29 built-in templates). Users want different analytical lenses: a standup needs different analysis than a sales call or interview. The current hardcoded prompt only produces one format.

#### How Granola Does It

- User types `/` to open a recipe picker menu
- "Discover" section with curated defaults; "My Recipes" for custom; "Shared" for team
- 29 built-in templates covering: Project Kick-Offs, Pipeline Reviews, 1:1s, Sprint Retros, Sales Calls, Customer Discovery, Interview Debriefs, etc.
- Recipe = system prompt + optional user prompt template
- Key principle: recipes only access meeting data, no external information

#### Recipe YAML Schema

```yaml
# Schema for recipe files
# Location: listener/recipes/*.yaml

recipes:
  - id: string          # unique, snake_case (e.g., "sales_call")
    name: string         # display name (e.g., "Sales Call Debrief")
    category: string     # general | sales | engineering | management | research | hiring | project
    description: string  # one-line description for picker UI
    system_prompt: string  # full system prompt sent to Claude
    user_prompt_template: string  # optional, default: "Analyze this meeting transcript:\n\n{transcript}"
    model: string        # optional, default: "claude-sonnet-4-5"
```

#### Built-in Recipes to Ship (8 templates)

**1. Standard Summary** (`standard_summary`)
Category: general
System prompt: Current default analysis prompt (summary, action items, decisions, topics).

**2. Sales Call Debrief** (`sales_call`)
Category: sales
Sections: Deal Summary, Signals & Sentiment, Objections Raised, Competitive Mentions, Next Steps, Coaching Notes.

**3. Sprint Retrospective** (`sprint_retro`)
Category: engineering
Sections: Sprint Overview, What Went Well, What Didn't Go Well, Action Items for Next Sprint, Team Morale.

**4. 1:1 Meeting Notes** (`one_on_one`)
Category: management
Sections: Status Updates, Blockers & Challenges, Feedback Exchanged, Career & Development, Action Items.

**5. Customer Discovery** (`customer_discovery`)
Category: research
Sections: Participant Profile, Pain Points (with direct quotes), Current Workflow, Feature Requests, Quotes Worth Saving (with timestamps), Opportunity Assessment.

**6. Interview Scorecard** (`interview_debrief`)
Category: hiring
Sections: Candidate Overview, Technical/Skill Assessment, Communication & Culture, Strengths, Concerns, Questions They Asked, Recommendation (Hire/No Hire/Further Discussion).

**7. Decision Log** (`decision_log`)
Category: project
For each decision: What, Why, Alternatives Considered, Owner, Timeline, Dependencies. Lists open questions if no decisions were made.

**8. Follow-up Email Draft** (`email_draft`)
Category: general
Outputs a ready-to-send email: subject line + body with summary bullets, action items, next meeting dates.

Full system prompts for all 8 recipes are documented in the research. Each prompt instructs Claude to respond in the same language as the transcript.

#### Implementation

**New file: `listener/recipes.py`** — recipe loader:

```python
import yaml
from pathlib import Path
from dataclasses import dataclass

BUILTIN_DIR = Path(__file__).parent / "recipes"
CUSTOM_DIR = Path.home() / ".listener" / "recipes"

@dataclass
class Recipe:
    id: str
    name: str
    category: str
    system_prompt: str
    description: str = ""
    user_prompt_template: str = "Analyze this meeting transcript:\n\n{transcript}"
    model: str = "claude-sonnet-4-5"
    is_builtin: bool = False

def load_recipes() -> list[Recipe]:
    recipes = []
    for directory, builtin in [(BUILTIN_DIR, True), (CUSTOM_DIR, False)]:
        if not directory.exists():
            continue
        for f in directory.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            for entry in data.get("recipes", []):
                entry["is_builtin"] = builtin
                recipes.append(Recipe(**entry))
    return recipes

def get_recipe(recipe_id: str) -> Recipe | None:
    for r in load_recipes():
        if r.id == recipe_id:
            return r
    return None
```

**Modify `listener/analyzer.py`:** Accept optional recipe parameter. If provided, use recipe's system_prompt and user_prompt_template instead of the hardcoded default.

**Modify `listener/web/app.py`:** Add `GET /api/recipes` endpoint. Add `recipe_id` parameter to the processing pipeline. Store selected recipe in session meta.json.

**Modify `listener/web/templates/index.html`:** Add recipe dropdown in settings card. Show selected recipe in session viewer.

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/recipes.py` | **Create** — recipe loader |
| `listener/recipes/default.yaml` | **Create** — 8 built-in recipe definitions |
| `listener/analyzer.py` | **Modify** — accept recipe parameter |
| `listener/web/app.py` | **Modify** — recipes endpoint, pipeline integration |
| `listener/web/templates/index.html` | **Modify** — recipe picker dropdown |
| `pyproject.toml` | **Modify** — add `pyyaml>=6.0` |

---

### F4: Full-Text Search Across Meetings

**Priority:** P2 (High)
**Effort:** Medium (2 days)
**Why:** Otter and Fireflies differentiate on cross-meeting search. Once users have 20+ meetings, finding "what was said about the budget in last week's standup" is the key workflow. Fathom's "Ask Fathom" also queries across all meetings.

#### Technical Approach: SQLite FTS5

Use SQLite with FTS5 (Full-Text Search 5). Zero additional dependencies — `sqlite3` is in Python's stdlib. FTS5 provides:
- BM25 ranking (relevance scoring)
- Snippet extraction with match highlighting
- Boolean queries (AND, OR, NOT)
- Phrase search (`"action items"`)
- Prefix search (`auto*`)
- Column-specific search (`title:standup`)
- NEAR queries (`NEAR(budget review, 5)`)

#### Database Schema

```sql
-- Main meetings table
CREATE TABLE meetings (
    session_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    date            TEXT NOT NULL,          -- ISO 8601
    duration        REAL DEFAULT 0,         -- seconds
    language        TEXT DEFAULT '',
    lang_confidence REAL DEFAULT 0,
    transcript      TEXT NOT NULL DEFAULT '',
    analysis        TEXT DEFAULT '',
    audio_path      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 virtual table
-- unicode61 tokenizer with remove_diacritics=2 for Turkish support
-- This maps ö->o, ü->u, ş->s etc. so searching "odul" matches "odül"
CREATE VIRTUAL TABLE meetings_fts USING fts5(
    title,
    transcript,
    analysis,
    content=meetings,
    content_rowid=rowid,
    tokenize='unicode61 remove_diacritics 2 tokenchars "'"
);

-- Auto-sync triggers (INSERT, UPDATE, DELETE)
-- [Full trigger SQL in implementation]
```

**Why `unicode61 remove_diacritics 2`:** Critical for Turkish. Users often search without special characters. `remove_diacritics=2` maps all Unicode diacritics, so "cogu" matches "çoğu", "gorus" matches "görüş". This is the recommended tokenizer for multilingual FTS5.

#### Query API

```python
def search_meetings(query: str, limit: int = 20) -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT m.session_id, m.title, m.date, m.duration, m.language,
            bm25(meetings_fts, 10.0, 1.0, 5.0) AS rank,
            snippet(meetings_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet
        FROM meetings_fts
        JOIN meetings m ON m.rowid = meetings_fts.rowid
        WHERE meetings_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()
    return [dict(r) for r in rows]
```

BM25 weights: title (10.0), transcript (1.0), analysis (5.0). Title matches are weighted highest because they're the strongest signal.

#### DB File Location

`~/.listener/listener.db` — persists across sessions, survives output directory changes.

#### Integration Points

- **On processing complete:** Call `insert_meeting()` to index the new meeting
- **Backfill:** On first run, scan `./transcripts/` and index any existing sessions not yet in the DB
- **Flask endpoint:** `GET /api/search?q=budget` returns ranked results with snippets
- **UI:** Add search bar at the top of the sessions list. Results show highlighted snippets and link to session viewer.

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/db.py` | **Create** — SQLite connection, schema init, CRUD, search |
| `listener/web/app.py` | **Modify** — search endpoint, insert on save, backfill on startup |
| `listener/web/templates/index.html` | **Modify** — search bar in sessions card, result rendering |

---

### F5: Click-to-Seek Audio Linkage

**Priority:** P1 (Medium-High)
**Effort:** Low (1 day)
**Why:** Scriberr and tl;dv have this. Very useful for verifying uncertain passages or re-hearing a specific moment. Our tool already has timestamps on every segment and an audio player — this just connects them.

#### Core Mechanism

HTML5 Audio API:
```javascript
const audio = document.querySelector('audio');
audio.currentTime = 125.5;  // seek to 2:05.5
audio.play();

audio.addEventListener('timeupdate', () => {
    // Fires ~4x/sec during playback
    const currentSeconds = audio.currentTime;
    // Highlight the transcript segment matching currentSeconds
});
```

#### Implementation

Three capabilities to add to `index.html`:

**1. Clickable timestamps** — Modify the inline markdown renderer to make `[HH:MM:SS]` timestamps into clickable spans:

```javascript
.replace(
    /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,
    '<span class="ts clickable" data-ts="$1" onclick="seekTo(\'$1\')">[$1]</span>'
);
```

Clicking calls `seekTo(tsStr)` which parses the timestamp to seconds and sets `audio.currentTime`.

**2. Auto-scroll during playback** — On the `timeupdate` event, find the segment whose `start` time is closest to (and <= ) `audio.currentTime`, add a highlight class (`seg-active`), and call `scrollIntoView({ behavior: 'smooth', block: 'center' })`. Include a toggle checkbox ("Auto-scroll") since users reading the transcript manually will want to disable this.

**3. Inline audio player on transcript tab** — When viewing the Transcript tab, pin an audio player at the top of the content area (above the transcript text). This lets users listen and read simultaneously without switching to the Audio tab.

#### CSS for Active Segment

```css
.seg-active {
    background: #eef2ff;
    border-left: 3px solid #6366f1;
    padding-left: 8px;
    margin-left: -11px;
    border-radius: 0 6px 6px 0;
    transition: background 0.3s ease;
}
.ts.clickable { cursor: pointer; transition: background 0.15s; }
.ts.clickable:hover { background: #c7d2fe; }
```

#### UX Pattern (from Scriberr and tl;dv)

- **tl;dv:** Transcript as scrolling sidebar alongside video. Clicking anywhere in a paragraph seeks to segment start. Auto-highlight + auto-scroll during playback.
- **Scriberr:** Timestamps on the left of each line. Click to jump. PWA-style works on mobile.
- **Recommended for Listener:** Audio player pinned at top of transcript content area. Each `[HH:MM:SS]` is a clickable link. During playback, active segment gets a left indigo border + light background. Auto-scroll on by default with toggle.

#### Files to Modify

| File | Action |
|---|---|
| `listener/web/templates/index.html` | **Modify** — JS: seekTo(), onTimeUpdate(), indexSegments(). CSS: .seg-active, .clickable. Modified loadTab() to embed audio player in transcript view |

---

### F6: Multi-Format Export

**Priority:** P2 (Medium-High)
**Effort:** Medium (2 days)
**Why:** Users share meeting notes across different tools. Markdown is great for developers but managers want PDF, Notion users want to copy-paste, and subtitle tools need SRT. Every major commercial tool supports multiple export formats.

#### Formats to Support

**1. DOCX (Word)**

Library: `python-docx>=1.1.0` (`pip install python-docx`)

Key implementation notes:
- Title as Heading 1
- Metadata (duration, language, date) as formatted paragraph
- Transcript: timestamps in blue Courier New, text in regular 11pt
- Analysis: parse markdown headers into Word heading styles, lists into Word list styles
- Page break between transcript and analysis sections

**2. PDF**

Library: `fpdf2>=2.8.0` (`pip install fpdf2`)

Key implementation notes:
- **Turkish character support requires bundling a Unicode TTF font.** DejaVuSans covers all Turkish characters (ğ, Ğ, ş, Ş, ç, Ç, ı, İ, ö, Ö, ü, Ü). Download from https://dejavu-fonts.github.io/
- Register font with `pdf.add_font("dejavu", "", "DejaVuSans.ttf")`
- Without a Unicode font, fpdf2 will fail on Turkish characters
- Store fonts in `listener/export/fonts/`

**3. SRT (Subtitles)**

Pure Python, no library needed. SRT format spec:
```
1
00:00:00,000 --> 00:00:05,230
First segment text here.

2
00:00:05,500 --> 00:00:15,100
Second segment text.
```

Rules:
- Sequence number: integer starting at 1
- Timestamp: `HH:MM:SS,mmm` (note: **comma** before milliseconds, not period)
- Arrow: ` --> ` (with spaces)
- Blank line separates entries
- UTF-8 encoding

If speaker diarization (F1) is implemented, prefix each segment with the speaker label.

**4. JSON (Structured)**

Pure Python. Schema for maximum interoperability:
```json
{
  "schema_version": "1.0",
  "generator": "listener",
  "exported_at": "2026-04-04T14:30:00Z",
  "metadata": {
    "session_id": "2026-04-04_14-30-00",
    "title": "Weekly Standup",
    "date": "2026-04-04",
    "duration_seconds": 2712.5,
    "language": "en",
    "language_confidence": 0.98
  },
  "transcript": {
    "full_text": "...",
    "segments": [
      {"index": 0, "start": 0.15, "end": 3.5, "text": "Hello everyone", "speaker": "SPEAKER_00"}
    ],
    "segment_count": 142
  },
  "analysis": {
    "content": "## Summary\n...",
    "model": "claude-sonnet-4-5",
    "recipe": "standard_summary"
  }
}
```

Use `json.dumps(doc, ensure_ascii=False, indent=2)` to preserve Turkish characters.

#### Flask Endpoints

```
GET /api/export/<session_id>?format=docx
GET /api/export/<session_id>?format=pdf
GET /api/export/<session_id>?format=srt
GET /api/export/<session_id>?format=json
```

Returns the file as a download.

#### UI Integration

Add export dropdown/buttons in:
1. Session viewer download row (alongside existing download buttons)
2. Session list context menu

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/export/__init__.py` | **Create** |
| `listener/export/docx_export.py` | **Create** |
| `listener/export/pdf_export.py` | **Create** |
| `listener/export/srt_export.py` | **Create** |
| `listener/export/json_export.py` | **Create** |
| `listener/export/fonts/DejaVuSans.ttf` | **Create** — download from dejavu-fonts.github.io |
| `listener/export/fonts/DejaVuSans-Bold.ttf` | **Create** |
| `listener/web/app.py` | **Modify** — add export endpoint |
| `listener/web/templates/index.html` | **Modify** — add export buttons with format selection |
| `pyproject.toml` | **Modify** — add `python-docx>=1.1.0`, `fpdf2>=2.8.0` |

---

### F7: Noise Preprocessing

**Priority:** P2 (Medium)
**Effort:** Low (1 day)
**Why:** Krisp's entire value proposition is noise cancellation + transcription. Noisy environments (cafes, open offices, echoing conference rooms) degrade Whisper accuracy significantly. A preprocessing step directly improves transcript quality.

#### Library

```
pip install noisereduce
```

Version: 3.0.3. Dependencies: numpy, scipy (already in our stack).

#### Implementation

```python
import soundfile as sf
import noisereduce as nr
import numpy as np

def preprocess_audio(input_path: str, output_path: str) -> str:
    """Apply noise reduction before transcription."""
    data, sr = sf.read(input_path)

    # Convert stereo to mono if needed
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    # Non-stationary noise reduction (adapts over time — good for meetings)
    cleaned = nr.reduce_noise(
        y=data,
        sr=sr,
        stationary=False,
        prop_decrease=0.75,    # 0.75 = moderate; 1.0 can distort speech
        time_constant_s=2.0,
        freq_mask_smooth_hz=500,
    )

    sf.write(output_path, cleaned, sr)
    return output_path
```

**Important:** Use `prop_decrease=0.75` not `1.0`. Full removal can distort speech harmonics and actually hurt Whisper accuracy. Non-stationary mode (`stationary=False`) adapts the noise floor over time, which is better for meetings where background noise varies.

#### Integration Point

In `_process_recording()` in `app.py`, add preprocessing between recording and transcription:

```python
# After recorder.stop(), before transcribe():
cleaned_path = audio_path.replace(".wav", "_cleaned.wav")
preprocess_audio(audio_path, cleaned_path)
# Pass cleaned_path to transcribe() instead of audio_path
# Keep original audio_path for playback (user hears the real audio)
```

#### User Control

Add `--no-denoise` CLI flag and "Enable noise reduction" checkbox in web UI settings. Default: enabled.

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/preprocessor.py` | **Create** — noise reduction function |
| `listener/web/app.py` | **Modify** — call preprocessor in pipeline |
| `listener/cli.py` | **Modify** — add `--no-denoise` flag |
| `listener/web/templates/index.html` | **Modify** — add checkbox in settings |
| `pyproject.toml` | **Modify** — add `noisereduce>=3.0.0` |

---

### F8: Meeting Analytics Dashboard

**Priority:** P3 (Medium)
**Effort:** Medium (2-3 days)
**Depends on:** F1 (Speaker Diarization) — talk-time analysis requires knowing who spoke when

**Why:** MeetGeek and Fireflies offer analytics dashboards. Managers want to see talk-time distribution, topic breakdown, and engagement patterns. Useful for coaching (is one person dominating?), meeting health (too many monologues?), and retrospectives.

#### Metrics to Compute

**From diarization (F1):**
- Talk time per speaker (seconds and percentage)
- Turn count per speaker (how many times each person spoke)
- Average turn length per speaker
- Interruption count (overlapping speech segments)
- Silence/gap ratio

**From Claude analysis:**
- Topic distribution (ask Claude to classify each segment into topics)
- Sentiment per speaker (positive/neutral/negative per segment)
- Question count per speaker

#### Storage

Add analytics to the session's `meta.json`:

```json
{
  "title": "Weekly Standup",
  "analytics": {
    "speakers": {
      "SPEAKER_00": {"talk_time": 1245.3, "turns": 42, "avg_turn_length": 29.6},
      "SPEAKER_01": {"talk_time": 890.7, "turns": 38, "avg_turn_length": 23.4}
    },
    "total_duration": 2712.5,
    "silence_ratio": 0.21,
    "topics": [
      {"name": "Budget Review", "duration": 600, "percentage": 22.1},
      {"name": "Q3 Planning", "duration": 450, "percentage": 16.6}
    ]
  }
}
```

#### UI

Add an "Analytics" tab in the session viewer. Use Chart.js (CDN, lightweight) for:
- Pie chart: talk-time distribution
- Bar chart: turns per speaker
- Timeline: speaker activity over meeting duration

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/analytics.py` | **Create** — compute metrics from diarized segments |
| `listener/web/app.py` | **Modify** — compute and store analytics after diarization |
| `listener/web/templates/index.html` | **Modify** — Analytics tab with Chart.js charts |

---

### F9: Real-Time Live Transcription

**Priority:** P4 (High impact but high effort)
**Effort:** High (3-5 days)
**Why:** Most commercial tools (Otter, Fireflies, Fathom, Tactiq) show text appearing as people speak. However, the user specified post-meeting processing is fine, so this is lower priority.

#### Architecture

This requires fundamentally rethinking the recording pipeline from "record everything, then transcribe" to "stream audio in chunks, transcribe each chunk live":

1. Record audio in chunks (e.g., 5-second sliding windows with 1-second overlap)
2. Send each chunk to faster-whisper for transcription
3. Push partial results to the browser via Server-Sent Events (SSE) or WebSocket
4. Merge/reconcile overlapping chunk transcriptions
5. Save final consolidated transcript when recording stops

**Technical approach (from WhisperLive):**
- WebSocket server streams audio from browser microphone (via Web Audio API)
- Server buffers audio and runs Whisper on 5-10 second windows
- Results pushed back to client via WebSocket
- Client displays streaming text with a "finalized" and "tentative" region

#### Libraries

- `flask-sock` or `flask-socketio` for WebSocket support
- Or use raw Server-Sent Events (SSE) with Flask's `Response(stream_with_context(generate()))`)

#### Why This is Hard

- Chunk boundary handling: words split across chunks produce garbled output
- Latency management: Whisper large-v3 takes 2-5 seconds per 5-second chunk on M4 CPU
- Memory: model must stay loaded in memory (3-4 GB) throughout recording
- Reconciliation: overlapping windows produce duplicate text that must be deduplicated
- VAD: need voice activity detection to avoid transcribing silence

This is a significant architecture change. Recommend implementing after all other features are stable.

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/streaming.py` | **Create** — chunked audio processing + Whisper streaming |
| `listener/web/app.py` | **Modify** — SSE/WebSocket endpoint for live updates |
| `listener/web/templates/index.html` | **Modify** — live transcript display with tentative/final regions |

---

### F10: Webhooks & API for Automation

**Priority:** P3 (Medium)
**Effort:** Low (1 day)
**Why:** MeetGeek (7000+ app integrations) and Fireflies power users build automated workflows. Webhooks enable Zapier/n8n/Slack/Notion integration without custom code.

#### Implementation

**Configuration:** Store webhook URLs in `~/.listener/config.yaml`:

```yaml
webhooks:
  - url: "https://hooks.slack.com/services/T.../B.../xxx"
    events: ["session_complete"]
    format: "slack"  # slack | json | markdown
  - url: "https://n8n.example.com/webhook/meeting-done"
    events: ["session_complete"]
    format: "json"
```

**Payload (JSON format):**

```json
{
  "event": "session_complete",
  "session_id": "2026-04-04_14-30-00",
  "title": "Weekly Standup",
  "duration": 2712.5,
  "language": "en",
  "summary": "First 500 chars of analysis...",
  "action_items": ["Item 1", "Item 2"],
  "files": {
    "transcript": "http://127.0.0.1:8642/api/download/..._transcript.md",
    "analysis": "http://127.0.0.1:8642/api/download/..._analysis.md"
  }
}
```

**Payload (Slack format):** Format as Slack Block Kit with sections for title, summary, and action items.

**Trigger point:** After `_process_recording()` completes successfully, fire webhooks asynchronously.

#### Flask Endpoints

```
GET  /api/webhooks           — list configured webhooks
POST /api/webhooks           — add a webhook
DELETE /api/webhooks/<id>    — remove a webhook
POST /api/webhooks/test/<id> — send a test payload
```

#### Files to Create/Modify

| File | Action |
|---|---|
| `listener/webhooks.py` | **Create** — load config, fire webhooks, format payloads |
| `listener/web/app.py` | **Modify** — webhook management endpoints, trigger after processing |
| `listener/web/templates/index.html` | **Modify** — webhook configuration UI (optional, can be config-file-only) |

---

## 5. Dependency Summary

### New Dependencies to Add

| Package | Version | Used By | Purpose |
|---|---|---|---|
| `pyannote.audio` | `>=3.1` | F1 | Speaker diarization |
| `torch` | (transitive via pyannote) | F1 | PyTorch backend |
| `pyyaml` | `>=6.0` | F3 | Recipe YAML parsing |
| `noisereduce` | `>=3.0.0` | F7 | Audio noise reduction |
| `python-docx` | `>=1.1.0` | F6 | DOCX export |
| `fpdf2` | `>=2.8.0` | F6 | PDF export with Unicode |

### Already Satisfied (no new deps)

| Feature | Why |
|---|---|
| F2: Chat | Uses existing Claude Code SDK |
| F4: Search | `sqlite3` is in Python stdlib |
| F5: Click-to-Seek | Pure JavaScript in frontend |
| F6: SRT/JSON export | Pure Python |
| F8: Analytics | Computation is pure Python; Chart.js loaded from CDN |
| F10: Webhooks | `urllib.request` or `requests` (already available) |

### External Requirements

| Requirement | Used By | Notes |
|---|---|---|
| HuggingFace account + read token | F1 | Must accept model licenses for pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0 |
| DejaVuSans TTF fonts | F6 (PDF) | Download from dejavu-fonts.github.io, bundle in `listener/export/fonts/` |
| ffmpeg | F6 (optional) | Only if adding video input support |

---

## 6. Architecture Notes

### Pipeline Flow (After All Features)

```
Recording (sounddevice)
    |
    v
Noise Preprocessing (F7: noisereduce)      [optional, toggle]
    |
    v
Transcription (faster-whisper large-v3)
    |
    v
Speaker Diarization (F1: pyannote-audio)   [optional, requires HF token]
    |
    v
Alignment (merge whisper segments + speaker labels)
    |
    v
Title Generation (Claude Code SDK)
    |
    v
Analysis (Claude Code SDK + Recipe F3)      [optional, toggle]
    |
    v
Save Files (transcript.md, analysis.md, meta.json)
    |
    v
Index in SQLite FTS5 (F4)
    |
    v
Fire Webhooks (F10)                         [if configured]
    |
    v
Done — UI shows viewer with all tabs (Transcript, Analysis, Chat, Audio, Analytics)
```

### File Organization (Target State)

```
listener/
├── pyproject.toml
├── listener/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── recorder.py
│   ├── preprocessor.py        # F7: noise reduction
│   ├── transcriber.py         # modified: speaker field in Segment
│   ├── diarizer.py            # F1: pyannote wrapper
│   ├── analyzer.py            # modified: recipe support
│   ├── chat.py                # F2: conversational Q&A
│   ├── db.py                  # F4: SQLite FTS5
│   ├── recipes.py             # F3: recipe loader
│   ├── analytics.py           # F8: meeting metrics
│   ├── webhooks.py            # F10: webhook dispatcher
│   ├── claude/
│   │   ├── __init__.py
│   │   └── runner.py
│   ├── recipes/
│   │   └── default.yaml       # F3: 8 built-in recipes
│   ├── export/
│   │   ├── __init__.py
│   │   ├── docx_export.py     # F6
│   │   ├── pdf_export.py      # F6
│   │   ├── srt_export.py      # F6
│   │   ├── json_export.py     # F6
│   │   └── fonts/
│   │       ├── DejaVuSans.ttf
│   │       └── DejaVuSans-Bold.ttf
│   └── web/
│       ├── __init__.py
│       ├── app.py             # modified: all new endpoints
│       └── templates/
│           └── index.html     # modified: all new UI components
```

### Key Design Principles

1. **Every feature is independently toggleable.** Diarization requires a HF token — users without one should still have a fully functional tool. Noise reduction can be disabled. Analysis can be skipped. Recipes are optional.

2. **The file-based approach remains the source of truth.** SQLite FTS5 is an index, not the primary store. If the DB is deleted, it can be rebuilt from the files in `./transcripts/`.

3. **Claude calls use the existing runner.py pattern.** All new Claude features (chat, title, recipes) go through `run_claude_session()` with the same Max OAuth auth, monkey-patched SDK, and timeout handling.

4. **UI stays as a single HTML file.** No build step, no npm, no bundler. All CSS/JS inline. External libraries (Chart.js) loaded from CDN. This keeps the "just run `listener web`" simplicity.

---

## 7. Sources & References

### Commercial Tools
- Otter.ai — https://otter.ai, https://bestaiprojecthub.com/execution-collaboration/otter-ai-overview-features
- Fireflies.ai — https://fireflies.ai
- Granola.ai — https://granola.ai, https://help.granola.ai/article/recipes, https://help.granola.ai/article/writing-effective-recipes
- tl;dv — https://tldv.io, https://tldv.io/timestamp-google-meet-calls/
- Fathom — https://fathom.video
- Krisp — https://krisp.ai
- Tactiq — https://tactiq.io
- MeetGeek — https://meetgeek.ai

### Open-Source Repos
- WhisperX — https://github.com/m-bain/whisperX (21.1k stars)
- Meetily — https://github.com/Zackriya-Solutions/meetily (10.9k stars)
- whisper-diarization — https://github.com/MahmoudAshraf97/whisper-diarization (5.5k stars)
- WhisperLive — https://github.com/collabora/WhisperLive (3.9k stars)
- Scriberr — https://github.com/rishikanthc/Scriberr (2.5k stars)

### Libraries & Technical References
- pyannote-audio — https://github.com/pyannote/pyannote-audio, https://huggingface.co/pyannote/speaker-diarization-3.1
- pyannote community-1 — https://huggingface.co/pyannote/speaker-diarization-community-1, https://www.pyannote.ai/blog/community-1
- pyannote MPS support — https://github.com/pyannote/pyannote-audio/discussions/1155
- pyannote memory usage — https://github.com/pyannote/pyannote-audio/issues/1580
- faster-whisper — https://github.com/SYSTRAN/faster-whisper
- noisereduce — https://github.com/timsainb/noisereduce, https://pypi.org/project/noisereduce/
- simple-diarizer — https://github.com/cvqluu/simple_diarizer
- SpeechBrain ECAPA-TDNN — https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- python-docx — https://pypi.org/project/python-docx/, https://python-docx.readthedocs.io/
- fpdf2 — https://pypi.org/project/fpdf2/, https://py-pdf.github.io/fpdf2/Unicode.html
- fpdf2 Turkish characters — https://github.com/py-pdf/fpdf2/issues/1281
- DejaVu fonts — https://dejavu-fonts.github.io/
- SQLite FTS5 — https://www.sqlite.org/fts5.html
- SQLite FTS5 unicode61 tokenizer — https://audrey.feldroy.com/articles/2025-01-13-SQLite-FTS5-Tokenizers-unicode61-and-ascii
- SQLite multilingual tokenization — https://www.slingacademy.com/article/configuring-sqlite-tokenizers-for-multilingual-text-search/
- SRT format spec — https://easylrc.com/blog/srt-format-technical-guide, https://en.wikipedia.org/wiki/SubRip
- HTML5 Audio API — https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime
- RAG vs Long Context — https://www.meilisearch.com/blog/rag-vs-long-context-llms
- Claude token counting — https://platform.claude.com/docs/en/build-with-claude/token-counting
