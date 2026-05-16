# Listener

Local-first meeting recorder, transcriber, and AI analyzer — runs end-to-end on your machine.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Status](https://img.shields.io/badge/status-beta-orange) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

Records audio with `sounddevice`, transcribes with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3`), identifies speakers with [pyannote-audio](https://github.com/pyannote/pyannote-audio) 3.1, and generates recipe-driven summaries through the [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code) over Max OAuth.

## Why this exists

Commercial meeting tools (Otter, Fireflies, Fathom, tl;dv, MeetGeek) require uploading audio to a vendor cloud and charge per-seat fees. Open-source alternatives either focus on a single capability (WhisperX = diarization-only, WhisperLive = streaming-only) or require heavyweight stacks (Rust + Next.js + Tauri). Listener fills the gap with a single `pip install -e .` Python package that runs entirely on a developer's laptop.

Audio and transcripts never leave the machine. The only network call is the analysis prompt (plain text) sent to Claude, and that ride is free for anyone with a Claude Max subscription — Listener forces the Claude Code SDK into OAuth mode so analysis costs $0 per call instead of metered API spend.

## Features

- **Speech-to-Text** — faster-whisper `large-v3` with auto language detection, VAD filter, word-level timestamps. Resumable via per-segment JSON checkpoints, so a 1-hour transcription survives Ctrl+C or laptop sleep.
- **Speaker Diarization** — pyannote-audio 3.1, aligned to Whisper segments with a max-overlap routine and deterministic friendly labels (Speaker 1, Speaker 2, ...).
- **AI Analysis** — Claude generates summaries, action items, decisions, and key topics. Same-language output enforced (transcript in Turkish → analysis in Turkish).
- **Chat with Transcript** — ask questions about a recording with cited `[HH:MM:SS]` timestamps.
- **Custom Analysis Recipes** — 8 built-in templates (sales call, sprint retro, 1:1, customer discovery, interview debrief, decision log, email draft, standard summary) plus user-defined YAML recipes from `~/.listener/recipes/`.
- **Full-Text Search** — SQLite FTS5 across all meetings with BM25-weighted ranking (title 10×, analysis 5×, transcript 1×) and `<mark>`-highlighted snippets. Unicode-aware tokenizer handles Turkish diacritics.
- **Click-to-Seek Audio** — click any timestamp in the web UI to jump the audio player to that moment.
- **Multi-Format Export** — DOCX, PDF (with bundled DejaVu fonts for Unicode/Turkish), SRT, JSON.
- **Noise Reduction** — `noisereduce` non-stationary preprocessing for noisy environments.
- **Meeting Analytics** — per-speaker talk-time, turn counts, silence ratio, with Chart.js visualizations.
- **Real-Time Live Transcription** — see text appear as people speak via Server-Sent Events (5s window, 2s overlap, finalized-vs-tentative segment separation, Jaccard dedup).
- **Webhooks** — JSON, Slack Block Kit, or Markdown payloads fired on `session_complete`, parallel delivery with 15s aggregate timeout.

## Architecture

```
Recorder (sounddevice)
  └─> Noise Reduction (noisereduce)            [optional]
       └─> Transcription (faster-whisper large-v3, resumable)
            └─> Speaker Diarization (pyannote-audio 3.1)  [optional]
                 └─> Title Generation (Claude Code SDK)
                      └─> Analytics (talk time, turns, silence ratio)
                           └─> Recipe Analysis (Claude Code SDK)  [optional]
                                └─> Files (.md / .json / .wav) + SQLite FTS5 index
                                     └─> Webhook fan-out (JSON / Slack / Markdown)
```

Three entry points share the same pipeline:

- **CLI** (`listener record`, `listener transcribe`, ...) — Click-based, 9 subcommands.
- **Flask web app** (`listener web`) — 25+ JSON endpoints + 1 SSE stream, single-file 2.5k-line HTML UI bound to `127.0.0.1` only.
- **Automation orchestrator** (`listener automate`) — multi-agent overnight feature pipeline driving the Claude Code SDK (planner → implementor → progress agent), with auto-resume and auto-commit.

All Claude calls funnel through `listener/claude/runner.py`, which forces Max-OAuth, monkey-patches the SDK message parser to tolerate unknown event types, and provides 3-attempt self-correcting JSON Schema validation.

## Install

Requirements:

- Python 3.11+
- macOS (developed and tested on Apple Silicon M4) or Linux
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) signed in with a Max subscription (for AI analysis and chat)
- A HuggingFace account + read token (only if you want speaker diarization)

```bash
git clone https://github.com/alperencngz/listener.git
cd listener
pip install -e .          # or: uv pip install -e .
```

### HuggingFace token (optional, for diarization)

1. Generate a read token at <https://huggingface.co/settings/tokens>.
2. Accept the model licenses for [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
3. Provide the token via one of:
   - `export HF_TOKEN=hf_xxx`
   - `~/.listener/config.yaml` → `hf_token: hf_xxx`
   - CLI flag: `listener record --hf-token hf_xxx`

## Usage

### Web UI (recommended)

```bash
listener web              # http://127.0.0.1:8642
listener web -p 3000      # custom port
```

### CLI

```bash
# Record a meeting (Ctrl+C to stop)
listener record

# Record with options
listener record --device 2 --language en --no-diarize --no-denoise --recipe sales_call

# Transcribe an existing audio file
listener transcribe meeting.wav

# Re-analyze an existing transcript with a different recipe
listener analyze transcripts/2026-04-04_14-30-00_transcript.md --recipe sprint_retro

# Search across all meetings
listener search "budget Q3"

# Export
listener export 2026-04-04_14-30-00 --format pdf
listener export 2026-04-04_14-30-00 --format docx --output-file notes.docx

# Utilities
listener recipes          # list available recipes
listener devices          # list audio input devices
```

### Custom recipes

Drop a YAML file in `~/.listener/recipes/`:

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

Configure in `~/.listener/config.yaml`:

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

## Tech stack

- **Python 3.11+**, [Click](https://click.palletsprojects.com/) CLI, [Flask 3](https://flask.palletsprojects.com/)
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** `large-v3` — CTranslate2-backed local STT with VAD and word timestamps
- **[pyannote-audio](https://github.com/pyannote/pyannote-audio)** 3.1 — speaker diarization (CPU)
- **[noisereduce](https://github.com/timsainb/noisereduce)** — non-stationary noise reduction
- **[Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python)** via Max OAuth — analysis, chat, automation agents
- **[jsonschema](https://github.com/python-jsonschema/jsonschema)** — structured-output validation with self-correcting retries
- **SQLite FTS5** — full-text search with BM25 ranking and Turkish-aware `unicode61` tokenizer
- **[sounddevice](https://python-sounddevice.readthedocs.io/) / [soundfile](https://python-soundfile.readthedocs.io/) / numpy** — audio I/O and DSP
- **[python-docx](https://python-docx.readthedocs.io/) / [fpdf2](https://py-pdf.github.io/fpdf2/)** — DOCX and Unicode-correct PDF export (bundled DejaVu fonts)
- **Chart.js** (CDN) — analytics visualizations in the single-file HTML UI

## Project structure

```
listener/
├── cli.py                 # Click CLI: record, transcribe, analyze, search, export, web, recipes, devices, automate
├── recorder.py            # sounddevice WAV capture, optional streaming callback
├── preprocessor.py        # noisereduce wrapper
├── transcriber.py         # faster-whisper + per-segment resume checkpoints
├── diarizer.py            # pyannote pipeline + Whisper-segment alignment
├── streaming.py           # Real-time sliding-window STT for SSE
├── analyzer.py            # Recipe-aware Claude analysis
├── chat.py                # Chat-with-transcript prompt builder
├── analytics.py           # Talk-time, turns, silence ratio
├── db.py                  # SQLite FTS5 index (BM25, snippet, backfill)
├── recipes.py             # YAML recipe loader (built-in + user)
├── webhooks.py            # JSON / Slack / Markdown fan-out
├── recipes/default.yaml   # 8 built-in analysis recipes
├── export/                # DOCX, PDF (DejaVu fonts), SRT, JSON via registry
├── claude/runner.py       # Claude Code SDK wrapper (Max OAuth, parser patch, schema retry)
├── automation/orchestrate.py  # Multi-agent overnight implementation pipeline
└── web/
    ├── app.py             # Flask: 25+ endpoints + SSE live stream
    └── templates/index.html  # Single-file vanilla-JS UI
```

## Notable design decisions

- **Max-OAuth zero-cost AI.** `listener/claude/runner.py` strips `ANTHROPIC_API_KEY` from the subprocess environment so the Claude Code SDK falls back to OAuth, billing AI analysis against an existing Claude Max subscription instead of the metered API. The SDK's `parse_message` is also monkey-patched at import time (idempotently) to tolerate unknown event types like `rate_limit_event` instead of crashing the consumer loop.
- **Local-only audio processing.** Recording, denoising, transcription, and diarization all run on-device. Only the text transcript is sent to Claude for analysis. The Flask server binds to `127.0.0.1` only and caps uploads at 2 GB.
- **Resumable transcription.** faster-whisper writes a JSON checkpoint after every segment with `last_end`, language, and the full segment list. On restart the transcriber loads the checkpoint and uses `clip_timestamps=[last_end]` to skip already-transcribed audio — a 1-hour run survives mid-flight interruption.
- **FTS5 BM25 with a Turkish-aware tokenizer.** The `meetings_fts` virtual table uses `unicode61 remove_diacritics 2`, BM25 weights `(title 10.0, transcript 1.0, analysis 5.0)`, `<mark>`-highlighted snippets, and an idempotent `backfill_from_transcripts()` that rebuilds the index from disk. The file tree is the source of truth.
- **Self-correcting structured output.** `run_with_schema_validation()` validates Claude's JSON against a `jsonschema.Draft7Validator` schema; on failure it reinjects the validator errors and the previous bad output into the prompt and retries up to 3 times.
- **Multi-agent overnight orchestration.** `listener automate` drives the Claude Code SDK through three agents per feature (planner Opus → implementor Opus → progress Sonnet) with per-agent `max_turns`, timeouts, and tool allowlists. It auto-resumes from `progress.md`, writes `feat(fN): ...` conventional commits, and exits non-zero so a shell retry loop (`check_and_restart.sh`) can rerun until the roadmap is complete.

## Data storage

| What                          | Where                                  |
| ----------------------------- | -------------------------------------- |
| Transcripts, audio, analysis  | `./transcripts/` (working directory)   |
| Search index (SQLite WAL)     | `~/.listener/listener.db`              |
| Config (HF token, webhooks)   | `~/.listener/config.yaml`              |
| Custom recipes                | `~/.listener/recipes/*.yaml`           |

Files are the source of truth — the SQLite index can be rebuilt at any time.

## Status

Beta. Single-author personal project, used as a daily driver. No external users, no published packages, no production deployment. Expect rough edges and breaking changes between versions.

## License

[MIT](./LICENSE) — © 2026 Alperen Cengiz Öztürk.
