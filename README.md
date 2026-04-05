# Listener

A local-first meeting recording, transcription, and analysis tool. Records audio, transcribes with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), identifies speakers with [pyannote-audio](https://github.com/pyannote/pyannote-audio), and generates AI-powered summaries with Claude.

**Privacy-first** — all transcription and audio processing happens locally on your machine. Only the AI analysis step uses Claude (via Claude Max OAuth — zero API cost).

## Features

- **Speech-to-Text** — faster-whisper large-v3 with auto language detection (English + Turkish + more)
- **Speaker Diarization** — pyannote-audio 3.1 identifies who said what, with color-coded labels
- **AI Analysis** — Claude generates summaries, action items, decisions, and key topics
- **Chat with Transcript** — ask questions about your meeting ("What did we decide about the timeline?")
- **Custom Analysis Recipes** — 8 built-in templates (sales call, sprint retro, 1:1, interview, etc.) + custom recipes
- **Full-Text Search** — SQLite FTS5 search across all your meetings with BM25 ranking
- **Click-to-Seek Audio** — click any timestamp to jump to that moment in the recording
- **Multi-Format Export** — DOCX, PDF (with Unicode/Turkish support), SRT subtitles, JSON
- **Noise Reduction** — noisereduce preprocessing for noisy environments (cafes, open offices)
- **Meeting Analytics** — talk-time distribution, turn counts, silence ratio with Chart.js visualizations
- **Real-Time Live Transcription** — see text appear as people speak via Server-Sent Events
- **Webhooks** — fire JSON/Slack/Markdown payloads to external services on session completion

## Requirements

- Python 3.11+
- macOS (tested on Apple Silicon M4)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) with Max OAuth (for AI analysis and chat)
- [HuggingFace account](https://huggingface.co) with read token (for speaker diarization — optional)

## Installation

```bash
git clone <repo-url>
cd listener
pip install -e .
```

### HuggingFace Token (optional, for speaker diarization)

1. Create an account at [huggingface.co](https://huggingface.co)
2. Generate a read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Accept model licenses:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
4. Provide the token via any of:
   - Environment variable: `export HF_TOKEN=hf_xxx`
   - Config file: add `hf_token: hf_xxx` to `~/.listener/config.yaml`
   - CLI flag: `listener record --hf-token hf_xxx`

## Usage

### Web Interface (recommended)

```bash
listener web
```

Opens at [http://127.0.0.1:8642](http://127.0.0.1:8642). Use `-p` for a custom port:

```bash
listener web -p 3000
```

### CLI Commands

```bash
# Record a meeting (press Ctrl+C to stop)
listener record

# Record with options
listener record --device 2 --language en --no-diarize --no-denoise --recipe sales_call

# Transcribe an existing audio file
listener transcribe meeting.wav

# Analyze an existing transcript
listener analyze transcript.md --recipe sprint_retro

# Search across all meetings
listener search "budget Q3"

# Export a meeting
listener export 2026-04-04_14-30-00 --format pdf
listener export 2026-04-04_14-30-00 --format docx --output-file notes.docx

# List available recipes
listener recipes

# List audio input devices
listener devices
```

### Export Formats

| Format | Command | Notes |
|--------|---------|-------|
| DOCX | `--format docx` | Word document with formatting and speaker labels |
| PDF | `--format pdf` | Unicode support via DejaVu fonts (Turkish, etc.) |
| SRT | `--format srt` | Subtitles with speaker labels |
| JSON | `--format json` | Structured data with full metadata |

### Custom Recipes

Built-in recipes:

| Recipe | Category | Description |
|--------|----------|-------------|
| `standard_summary` | general | Default summary with action items and decisions |
| `sales_call` | sales | Deal summary, objections, competitive mentions, coaching notes |
| `sprint_retro` | engineering | What went well, what didn't, action items for next sprint |
| `one_on_one` | management | Status updates, blockers, feedback, career development |
| `customer_discovery` | research | Pain points, feature requests, direct quotes with timestamps |
| `interview_debrief` | hiring | Skill assessment, strengths, concerns, hire recommendation |
| `decision_log` | project | Decisions made, alternatives considered, owners, timelines |
| `email_draft` | general | Ready-to-send follow-up email with summary and action items |

Create custom recipes by adding YAML files to `~/.listener/recipes/`:

```yaml
recipes:
  - id: my_recipe
    name: My Custom Recipe
    category: general
    description: A custom analysis template
    system_prompt: |
      You are a meeting analyst. Analyze this transcript and produce...
    user_prompt_template: "Analyze this meeting transcript:\n\n{transcript}"
```

### Webhooks

Configure webhooks in `~/.listener/config.yaml`:

```yaml
webhooks:
  - url: "https://hooks.slack.com/services/T.../B.../xxx"
    events: ["session_complete"]
    format: "slack"
  - url: "https://n8n.example.com/webhook/meeting-done"
    events: ["session_complete"]
    format: "json"
```

Supported formats: `json`, `slack` (Block Kit), `markdown`.

## Architecture

```
Recording (sounddevice)
    |
Noise Reduction (noisereduce)         [optional]
    |
Transcription (faster-whisper)
    |
Speaker Diarization (pyannote-audio)  [optional]
    |
Title Generation (Claude)
    |
Analytics Computation
    |
AI Analysis with Recipe (Claude)      [optional]
    |
Save Files + Index in SQLite FTS5
    |
Fire Webhooks                         [if configured]
```

All features are independently toggleable. Diarization, noise reduction, and analysis can each be disabled via CLI flags or web UI checkboxes.

## Project Structure

```
listener/
├── cli.py                 # CLI entry point (click)
├── recorder.py            # Audio recording with sounddevice
├── preprocessor.py        # Noise reduction (noisereduce)
├── transcriber.py         # Speech-to-text (faster-whisper)
├── diarizer.py            # Speaker diarization (pyannote-audio)
├── analyzer.py            # AI analysis (Claude)
├── chat.py                # Chat with transcript (Claude)
├── recipes.py             # Recipe loader
├── db.py                  # SQLite FTS5 search index
├── analytics.py           # Meeting metrics computation
├── streaming.py           # Real-time live transcription
├── webhooks.py            # Webhook dispatcher
├── recipes/
│   └── default.yaml       # 8 built-in analysis recipes
├── export/
│   ├── __init__.py        # Format registry
│   ├── docx_export.py
│   ├── pdf_export.py
│   ├── srt_export.py
│   ├── json_export.py
│   └── fonts/             # DejaVu fonts for PDF Unicode
├── claude/
│   └── runner.py          # Claude Code SDK wrapper
└── web/
    ├── app.py             # Flask server
    └── templates/
        └── index.html     # Single-file web UI
```

## Data Storage

| What | Where |
|------|-------|
| Transcripts, audio, analysis | `./transcripts/` (working directory) |
| Search index | `~/.listener/listener.db` |
| Config (HF token, webhooks) | `~/.listener/config.yaml` |
| Custom recipes | `~/.listener/recipes/*.yaml` |

File-based storage is the source of truth. The SQLite database is a secondary search index and can be rebuilt from files.

## Tech Stack

- **Python 3.11+** with Click, Flask
- **faster-whisper** (large-v3) — local speech-to-text
- **pyannote-audio 3.1** — speaker diarization
- **noisereduce** — audio preprocessing
- **Claude Code SDK** (Max OAuth) — AI analysis and chat
- **SQLite FTS5** — full-text search
- **Chart.js** (CDN) — analytics visualizations
- **Single HTML file** — no build step, no npm, no bundler

## License

Private project.
