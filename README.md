# Listener

Local-first meeting recorder, transcriber, and AI analyzer — runs end-to-end on your machine.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Status](https://img.shields.io/badge/status-beta-orange) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

Records audio with `sounddevice`, transcribes with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3`), identifies speakers with [pyannote-audio](https://github.com/pyannote/pyannote-audio) 3.1, and generates recipe-driven summaries through the [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code) over Max OAuth.

## Why this exists

Commercial meeting tools (Otter, Fireflies, Fathom, tl;dv, MeetGeek) require uploading audio to a vendor cloud and charge per-seat fees. Open-source alternatives either focus on a single capability (WhisperX = diarization-only, WhisperLive = streaming-only) or require heavyweight stacks (Rust + Next.js + Tauri). Listener fills the gap with a single `pip install -e .` Python package that runs entirely on a developer's laptop.

Audio and transcripts never leave the machine. The only network call is the analysis prompt (plain text) sent to Claude, and that ride is free for anyone with a Claude Max subscription — Listener forces the Claude Code SDK into OAuth mode so analysis costs $0 per call instead of metered API spend.

## Features

- **Record while transcribing** — recording, the processing queue and each meeting's job state are independent. Start recording meeting B while meeting A is still being transcribed. One microphone recording at a time; one transcription job at a time.
- **Manual processing queue** — stopping a recording or importing audio only saves the file. You choose which recordings to queue, then press **Run queued** or **Run selected**. Jobs run one after another; jobs added during a run wait for the next run. Queue state lives in SQLite and survives refreshes and restarts; interrupted jobs stay visible and need an explicit **Retry**.
- **Explicit Claude actions only** — transcription never calls Claude. Recipe analysis (**Analyze with Claude**) and meeting memory (**Generate memory** / **Update memory**) are buttons you press per meeting.
- **Meeting memory bank** — a grounded, inspectable record per meeting: summary, key points, decisions with rationale, to-dos with owner/deadline, open questions, each tied to transcript timestamps. Stored in SQLite and mirrored to `<id>_memory.md` / `.json`. Manual edits and completed to-dos survive re-generation; nothing is deleted by the AI.
- **Ask across meetings** — search or pick meetings (or save a project grouping) and ask Claude to summarise shared context, recall decisions or list outstanding work. Only the stored memory of the selected meetings is sent (bounded), and answers cite the source meetings.
- **Speech-to-Text** — faster-whisper `large-v3` with auto language detection or per-window mixed Turkish/English detection, VAD filter, word-level timestamps. Resumable via per-segment JSON checkpoints; a stopped or interrupted job resumes from the checkpoint on retry.
- **Speaker Diarization** — pyannote-audio 3.1 (optional, needs a HuggingFace token), aligned to Whisper segments with deterministic labels (Speaker 1, Speaker 2, ...).
- **Chat with Transcript** — ask questions about one recording with cited `[HH:MM:SS]` timestamps.
- **Custom Analysis Recipes** — 8 built-in templates plus user-defined YAML recipes from `~/.listener/recipes/`.
- **Full-Text Search** — SQLite FTS5 across all meetings (and across meeting memory) with BM25 ranking and highlighted snippets. Unicode-aware tokenizer handles Turkish diacritics.
- **Click-to-Seek Audio**, **Multi-Format Export** (DOCX, PDF, SRT, JSON), **Noise Reduction**, **Meeting Analytics**, **Real-Time Live Transcription** (optional), **Webhooks** — unchanged from earlier versions.

## How a meeting flows through Listener

```
Record (mic)  ──stop──▶  saved WAV + meta        ─┐
Import audio  ──────────▶  saved WAV + meta        ├─▶  "Add to queue"  ──▶  Processing queue (SQLite)
                                                    │                          │
                                                    │        you press "Run queued" / "Run selected"
                                                    │                          ▼
                                                    │   denoise → Whisper (resumable) → diarize → transcript + index
                                                    │                          │
                                                    │        you press "Analyze with Claude" / "Generate memory"
                                                    │                          ▼
                                                    └──────────▶   analysis.md   /   memory bank (SQLite + memory.md)
```

Nothing moves from one stage to the next without a click. Three lanes never block each other:

| Lane | What runs | Concurrency |
| --- | --- | --- |
| Recording | `sounddevice` → WAV | one recording at a time |
| Transcription run | queued `transcribe` jobs, sequentially | one job at a time, one run at a time |
| Claude actions | `analyze` / `memory` jobs | one at a time, start immediately when you click |

Within one meeting, jobs are also exclusive: if a transcription and a Claude job for the same meeting would
overlap (for example a queued re-transcribe and an analysis started meanwhile), the second one to start is
marked **failed** with a "Skipped" note instead of writing the meeting's files at the same time. Retry it
once the other job has finished. "Ask" questions run in the web request itself, not through the queue.

**Live transcription under contention.** Live transcription is optional and loads its own Whisper model in the recording process. To avoid two large models competing for the laptop, recording always wins: if a transcription run is active when you start a recording with live transcription, the recording starts normally and live text is skipped (the UI tells you why). Conversely, the queue refuses to run while live transcription is active; stop the recording (or record without live mode) first.

Three entry points share the same pipeline code (`listener/pipeline.py`):

- **CLI** (`listener record`, `listener transcribe`, `listener memory ...`) — Click-based.
- **Flask web app** (`listener web`) — JSON endpoints + 1 SSE stream, single-file HTML UI bound to `127.0.0.1` only.
- **Automation orchestrator** (`listener automate`) — the older overnight feature pipeline (unchanged).

All Claude calls funnel through `listener/claude/runner.py` (Max OAuth, tolerant SDK parser, schema-validated JSON with self-correcting retries).

## Install

Requirements:

- Python 3.11+
- macOS (developed and tested on Apple Silicon M4) or Linux
- For AI analysis, memory and chat: either the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) signed in with your subscription, or an Anthropic API key (Settings → Claude access)
- A HuggingFace account + read token (only if you want speaker diarization)

```bash
git clone https://github.com/alperencngz/listener.git
cd listener
uv sync --extra desktop   # or: pip install -e .   (the desktop extra adds the native window)
```

### Run it as a Mac app

Point an AI coding agent at **[AGENT_SETUP.md](AGENT_SETUP.md)** ("Read AGENT_SETUP.md and
follow it to install Listener on this Mac"), or do it by hand:

```bash
./mac/build_app.sh
cp -R dist/Listener.app /Applications/
```

`Listener.app` is a py2app alias-mode bundle, the same approach as Dictator: it runs this
repo's source and `.venv` in place, so it only works on the machine you built it on, and
`git pull` updates take effect on the next launch without a rebuild. The app shows the web UI
in its own window (`listener desktop` does the same from a terminal), stores meetings in
`~/Documents/Listener` unless `data_dir` is set in `~/.listener/config.yaml`, preselects the
`large-v3-turbo` model, and asks before quitting while a recording or a transcription is
running. Exports and downloads open a Save dialog. Microphone access is granted to the app
itself the first time you press Record. The Input Device list is re-read from the OS whenever
the window gets focus and whenever you press Record, the system default microphone is
preselected, and your choice is remembered by name; if that device has disconnected (an
iPhone or Bluetooth microphone, say) the recording falls back to the default microphone and
says so under the Record button.

### Tags

Tags are your own labels for meetings (`client`, `1:1`, `voice memo`, ...). Tag a recording right
after you stop it, an uploaded memo right after the import, or any meeting from its header; type a
new name to create a tag on the spot. Each tag can carry a one-line note in Settings, and that note
is what Claude receives: every analysis, memory generation and cross-meeting question gets the
meeting's tags with their notes as user-provided context, clearly separated from the transcript.
The sidebar filters by tag. The vocabulary lives in `~/.listener/config.yaml` under `tags`, a
meeting's tags in its `_meta.json`, and the CLI takes `--tag` on `record` and `transcribe`.

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

Typical session:

1. **Record** → **Stop**. The recording is saved; name it inline.
2. **Add to queue** (uses the Language / noise-reduction settings at that moment). Import audio the same way.
3. **Run queued** (or tick some jobs and **Run selected**). You can start the next recording while this runs. **Stop** interrupts after the current segment and keeps the checkpoint; **Retry** resumes.
   Settings (model, language, noise reduction, Claude access) apply at the moment you add a job; the model and the Claude choice are saved to `~/.listener/config.yaml`.
4. Open the meeting → **Analysis** tab → pick a recipe → **Analyze with Claude**.
5. **Memory** tab → **Generate memory**. Tick to-dos as you complete them, edit owners/deadlines; **Update memory** later keeps your edits.
6. Sidebar **Memory** → search/select meetings or a project → ask *"What is still open?"*.

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

# Meeting memory (explicit Claude call per meeting)
listener memory generate 2026-04-04_14-30-00
listener memory show 2026-04-04_14-30-00
listener memory tasks --open
listener memory ask "What is still open?" --meeting 2026-04-04_14-30-00 --meeting 2026-04-05_10-47-34
listener memory projects create "Q3 launch" --meeting 2026-04-04_14-30-00

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

`session_complete` fires when a transcript has been saved by the queue (the payload's `files` includes
`analysis` only if an analysis file already exists). Analysis and memory are explicit Claude actions and do
not fire webhooks. Earlier versions fired this event once after title + analysis; adjust subscribers that
expected the analysis to be attached.

## Tech stack

- **Python 3.11+**, [Click](https://click.palletsprojects.com/) CLI, [Flask 3](https://flask.palletsprojects.com/)
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** `large-v3` — CTranslate2-backed local STT with VAD and word timestamps
- **[pyannote-audio](https://github.com/pyannote/pyannote-audio)** 3.1 — speaker diarization (CPU)
- **[noisereduce](https://github.com/timsainb/noisereduce)** — non-stationary noise reduction
- **[Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python)** via Max OAuth, or the **[Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python)** with an API key — analysis, memory, chat
- **[pywebview](https://pywebview.flowrl.com/)** + **py2app** (alias mode) — the Mac app window and bundle (`mac/`)
- **[jsonschema](https://github.com/python-jsonschema/jsonschema)** — structured-output validation with self-correcting retries
- **SQLite FTS5** — full-text search with BM25 ranking and Turkish-aware `unicode61` tokenizer
- **[sounddevice](https://python-sounddevice.readthedocs.io/) / [soundfile](https://python-soundfile.readthedocs.io/) / numpy** — audio I/O and DSP
- **[python-docx](https://python-docx.readthedocs.io/) / [fpdf2](https://py-pdf.github.io/fpdf2/)** — DOCX and Unicode-correct PDF export (bundled DejaVu fonts)
- **Chart.js** (CDN) — analytics visualizations in the single-file HTML UI

## Project structure

```
listener/
├── cli.py                 # Click CLI: record, transcribe, analyze, search, export, web, desktop, recipes, devices, automate
├── settings.py            # ~/.listener/config.yaml: data_dir, default_model, Claude access mode / API key
├── desktop.py             # native window (pywebview) around the Flask app; what Listener.app runs
├── recorder.py            # sounddevice WAV capture, optional streaming callback
├── preprocessor.py        # noisereduce wrapper
├── transcriber.py         # faster-whisper + per-segment resume checkpoints (stop/progress hooks)
├── jobs.py                # persistent job queue (SQLite) + sequential runner
├── pipeline.py            # job executors: transcribe (no Claude), analyze, memory
├── memory.py              # meeting memory bank: grounded generation, task merge, retrieval, ask
├── memory_cli.py          # `listener memory ...` commands
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
    ├── app.py             # Flask: recording, queue, Claude actions, memory, SSE live stream
    └── templates/index.html  # Single-file vanilla-JS UI (queue panel, Memory tab/view)
```

## Notable design decisions

- **Independent state, explicit transitions.** The web app keeps recording state, the transcription run and per-meeting job rows apart (`listener/jobs.py`). Jobs are created only by user actions and executed only by an explicit run; on startup any job left `running` by a crashed process is marked `interrupted` and waits for **Retry** — paid Claude calls are never silently re-run. Duplicate submissions and transcript overwrites are refused unless you choose **Re-transcribe**.
- **One process, a few locks.** The web app shares one SQLite connection between request threads, the run thread and the Claude worker; `listener/db.py` wraps it so every statement runs (and is fully fetched) under a re-entrant lock, and multi-statement updates hold the same lock. `meta.json` merges go through `META_LOCK`, files are written via uniquely named temp files + rename, and a recording slot / session id is reserved atomically before the microphone or the live Whisper model opens, so two clicks in the same second cannot open two microphones or share an id.
- **Grounded memory, mechanically checked.** Claude returns JSON (schema-validated) for the memory bank, then `listener/memory.py` verifies every timestamp against the transcript and drops owners/deadlines that do not appear as whole words in the spoken lines (the file header does not count), recording what was dropped in `grounding_notes`. Transcript text is wrapped as data with an explicit "not instructions" rule. Re-generation merges to-dos by text similarity: completed/edited/hand-added tasks keep their wording and state, vanished tasks are marked stale, never deleted; a generation is one transaction, so a failure mid-way leaves the previous memory untouched.
- **Bounded cross-meeting retrieval.** "Ask" sends only the stored memory of the selected meetings (per-meeting and total character caps, meetings beyond the cap are listed as omitted), never the whole archive or raw transcripts, and answers cite meeting labels.
- **Max-OAuth zero-cost AI.** `listener/claude/runner.py` strips `ANTHROPIC_API_KEY` from the subprocess environment so the Claude Code SDK falls back to OAuth, billing AI analysis against an existing Claude Max subscription instead of the metered API. The SDK's `parse_message` is also monkey-patched at import time (idempotently) to tolerate unknown event types like `rate_limit_event` instead of crashing the consumer loop.
- **Local-only audio processing.** Recording, denoising, transcription, and diarization all run on-device. Only the text transcript is sent to Claude for analysis. The Flask server binds to `127.0.0.1` only and caps uploads at 2 GB.
- **Resumable transcription.** faster-whisper writes a JSON checkpoint after every segment with `last_end`, language, and the full segment list. On restart the transcriber loads the checkpoint and uses `clip_timestamps=[last_end]` to skip already-transcribed audio — a 1-hour run survives mid-flight interruption.
- **FTS5 BM25 with a Turkish-aware tokenizer.** The `meetings_fts` virtual table uses `unicode61 remove_diacritics 2`, BM25 weights `(title 10.0, transcript 1.0, analysis 5.0)`, `<mark>`-highlighted snippets, and an idempotent `backfill_from_transcripts()` that rebuilds the index from disk. The file tree is the source of truth.
- **Self-correcting structured output.** `run_with_schema_validation()` validates Claude's JSON against a `jsonschema.Draft7Validator` schema; on failure it reinjects the validator errors and the previous bad output into the prompt and retries up to 3 times.
- **Multi-agent overnight orchestration.** `listener automate` drives the Claude Code SDK through three agents per feature (planner Opus → implementor Opus → progress Sonnet) with per-agent `max_turns`, timeouts, and tool allowlists. It auto-resumes from `progress.md`, writes `feat(fN): ...` conventional commits, and exits non-zero so a shell retry loop (`check_and_restart.sh`) can rerun until the roadmap is complete.

## Data storage

| What                          | Where                                  |
| ----------------------------- | -------------------------------------- |
| Transcripts, audio, analysis, memory.md/json | `./transcripts/` (working directory) |
| Search index, processing queue, memory bank (SQLite WAL) | `~/.listener/listener.db` |
| Config (HF token, webhooks)   | `~/.listener/config.yaml`              |
| Custom recipes                | `~/.listener/recipes/*.yaml`           |

Files are the source of truth — the SQLite index can be rebuilt at any time.

## Status

Beta. Single-author personal project, used as a daily driver. No external users, no published packages, no production deployment. Expect rough edges and breaking changes between versions.

## License

[MIT](./LICENSE) — © 2026 Alperen Cengiz Öztürk.
