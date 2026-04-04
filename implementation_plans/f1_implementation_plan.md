# Implementation Plan: F1 -- Speaker Diarization

## Status: ALREADY IMPLEMENTED

> **Important:** According to `/Users/alperencngzz/Desktop/listener/progress.md`, F1 was completed on 2026-04-04. All code changes described below are already present in the codebase. This plan serves as documentation and a verification reference. If you are re-implementing or fixing issues, use the task details below.

---

## Pre-Implementation Checklist

- [ ] Read these files fresh (they contain F1 changes already):
  - `/Users/alperencngzz/Desktop/listener/listener/diarizer.py` (created by F1)
  - `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` (modified by F1)
  - `/Users/alperencngzz/Desktop/listener/listener/cli.py` (modified by F1)
  - `/Users/alperencngzz/Desktop/listener/listener/web/app.py` (modified by F1)
  - `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` (modified by F1)
  - `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- [ ] Verify `pyannote.audio>=3.1` and `torch>=2.0.0` are in `pyproject.toml` dependencies
- [ ] Verify a HuggingFace token is available via `HF_TOKEN` env var, `HUGGINGFACE_TOKEN` env var, or `~/.listener/config.yaml`
- [ ] Verify the user has accepted model licenses at:
  - https://huggingface.co/pyannote/speaker-diarization-3.1
  - https://huggingface.co/pyannote/segmentation-3.0

---

## Dependencies

### Already in `pyproject.toml` (confirmed):

```toml
dependencies = [
    ...
    "pyannote.audio>=3.1",
    "torch>=2.0.0",
]
```

### Install command:

```bash
cd /Users/alperencngzz/Desktop/listener
pip install -e .
```

### External requirements:

- **HuggingFace account** with a read-access token
- Accept license for `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0` on HuggingFace

---

## Implementation Tasks

### Task 1: Segment dataclass with speaker field

- **File:** `/Users/alperencngzz/Desktop/listener/listener/transcriber.py`
- **Action:** Modify (ALREADY DONE)
- **Details:** The `Segment` dataclass has a `speaker` field with default `""`. `TranscriptionResult` has a `has_speakers` property. `to_timestamped_text()` outputs speaker labels when present.
- **Current code (lines 11-18):**

```python
@dataclass
class Segment:
    """A transcribed speech segment."""
    start: float
    end: float
    text: str
    speaker: str = ""
```

- **`has_speakers` property (line 34-35):**

```python
@property
def has_speakers(self) -> bool:
    return any(s.speaker for s in self.segments)
```

- **`to_timestamped_text()` with speaker labels (lines 37-45):**

```python
def to_timestamped_text(self) -> str:
    """Format as timestamped transcript lines, with speaker labels if available."""
    lines = []
    for seg in self.segments:
        ts = _fmt_ts(seg.start)
        if seg.speaker:
            lines.append(f"**[{ts}] {seg.speaker}:** {seg.text}")
        else:
            lines.append(f"[{ts}] {seg.text}")
    return "\n\n".join(lines)
```

- **Verification:**

```bash
python -c "from listener.transcriber import Segment, TranscriptionResult; s = Segment(0, 1, 'hi', 'Speaker 1'); r = TranscriptionResult([s], 'en', 0.99, 60); print(r.has_speakers); print(r.to_timestamped_text())"
```

Expected output:
```
True
**[00:00] Speaker 1:** hi
```

---

### Task 2: Diarizer module

- **File:** `/Users/alperencngzz/Desktop/listener/listener/diarizer.py`
- **Action:** Create (ALREADY DONE)
- **Details:** Complete module with 5 functions:

1. **`get_hf_token(cli_token=None) -> str | None`** -- Resolves HuggingFace token from CLI arg > `HF_TOKEN` env > `HUGGINGFACE_TOKEN` env > `~/.listener/config.yaml`

2. **`load_pipeline(hf_token, device="cpu")`** -- Loads and caches (global `_pipeline`) the `pyannote/speaker-diarization-3.1` pipeline. Uses `Pipeline.from_pretrained()` with `use_auth_token`.

3. **`diarize(audio_path, hf_token, device="cpu", min_speakers=None, max_speakers=None)`** -- Runs diarization on a WAV file. Returns a `pyannote.core.Annotation` object.

4. **`align_speakers(diarization, whisper_segments) -> list[Segment]`** -- For each Whisper segment, finds the pyannote speaker with the most temporal overlap. Maps raw labels (`SPEAKER_00`) to human-friendly labels (`Speaker 1`, `Speaker 2`, etc.).

5. **`compute_talk_times(segments) -> dict[str, float]`** -- Computes total talk time per speaker in seconds.

- **Full source code:**

```python
"""Speaker diarization using pyannote-audio.

Uses pyannote/speaker-diarization-3.1 to identify who spoke when,
then aligns speaker labels with Whisper transcript segments.
"""

import os
from pathlib import Path

from listener.transcriber import Segment


def get_hf_token(cli_token: str | None = None) -> str | None:
    """Resolve HuggingFace token from CLI arg, env var, or config file.

    Priority:
        1. cli_token argument (from --hf-token flag)
        2. HF_TOKEN environment variable
        3. HUGGINGFACE_TOKEN environment variable
        4. ~/.listener/config.yaml hf_token field

    Returns:
        Token string, or None if not found.
    """
    if cli_token:
        return cli_token

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        return token

    config_path = Path.home() / ".listener" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            token = cfg.get("hf_token")
            if token:
                return str(token)
        except Exception:
            pass

    return None


_pipeline = None


def load_pipeline(hf_token: str, device: str = "cpu"):
    """Load and cache the pyannote speaker diarization pipeline.

    Args:
        hf_token: HuggingFace authentication token.
        device: 'cpu' or 'mps'. CPU recommended for 16GB machines.
    """
    global _pipeline

    if _pipeline is not None:
        return _pipeline

    import torch
    from pyannote.audio import Pipeline

    print("Loading pyannote speaker diarization model (first run downloads ~600MB)...")
    _pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    _pipeline.to(torch.device(device))
    print("Diarization model loaded.")
    return _pipeline


def diarize(
    audio_path: str,
    hf_token: str,
    device: str = "cpu",
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> "Annotation":
    """Run speaker diarization on an audio file.

    Args:
        audio_path: Path to WAV audio file.
        hf_token: HuggingFace token.
        device: 'cpu' or 'mps'.
        min_speakers: Minimum expected speakers (optional).
        max_speakers: Maximum expected speakers (optional).

    Returns:
        pyannote.core.Annotation object with speaker segments.
    """
    pipeline = load_pipeline(hf_token, device=device)

    print(f"Running speaker diarization on {Path(audio_path).name}...")

    kwargs = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    diarization = pipeline(audio_path, **kwargs)

    # Count speakers found
    speakers = set()
    for _, _, speaker in diarization.itertracks(yield_label=True):
        speakers.add(speaker)
    print(f"Diarization complete: {len(speakers)} speakers detected.")

    return diarization


def align_speakers(diarization, whisper_segments: list[Segment]) -> list[Segment]:
    """Align pyannote diarization results with Whisper transcript segments.

    For each Whisper segment, finds the pyannote speaker with the most
    temporal overlap and assigns that speaker label.

    Args:
        diarization: pyannote Annotation object from diarize().
        whisper_segments: List of Segment objects from transcriber.

    Returns:
        New list of Segment objects with speaker field populated.
        Speaker labels are human-friendly: "Speaker 1", "Speaker 2", etc.
    """
    # Build speaker label mapping (SPEAKER_00 -> Speaker 1, etc.)
    raw_speakers = set()
    for _, _, speaker in diarization.itertracks(yield_label=True):
        raw_speakers.add(speaker)

    sorted_speakers = sorted(raw_speakers)  # deterministic order
    speaker_map = {
        raw: f"Speaker {i + 1}" for i, raw in enumerate(sorted_speakers)
    }

    result = []
    for seg in whisper_segments:
        # Calculate overlap with each speaker
        speaker_durations: dict[str, float] = {}
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            overlap_start = max(seg.start, turn.start)
            overlap_end = min(seg.end, turn.end)
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > 0:
                speaker_durations[speaker] = speaker_durations.get(speaker, 0.0) + overlap

        if speaker_durations:
            best_raw = max(speaker_durations, key=speaker_durations.get)
            best_speaker = speaker_map.get(best_raw, "Unknown")
        else:
            best_speaker = "Unknown"

        result.append(Segment(
            start=seg.start,
            end=seg.end,
            text=seg.text,
            speaker=best_speaker,
        ))

    return result


def compute_talk_times(segments: list[Segment]) -> dict[str, float]:
    """Compute total talk time per speaker in seconds.

    Args:
        segments: List of Segment objects with speaker labels.

    Returns:
        Dict mapping speaker label to total seconds spoken.
    """
    times: dict[str, float] = {}
    for seg in segments:
        label = seg.speaker or "Unknown"
        duration = seg.end - seg.start
        times[label] = times.get(label, 0.0) + duration
    return times
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.diarizer import get_hf_token, align_speakers, compute_talk_times; print('diarizer module imports OK')"`

---

### Task 3: CLI flags for diarization

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** Modify (ALREADY DONE)
- **Details:**

Both `record` and `transcribe` commands have these options:

```python
@click.option("--hf-token", default=None,
              help="HuggingFace token for speaker diarization (or set HF_TOKEN env var)")
@click.option("--no-diarize", is_flag=True,
              help="Skip speaker diarization even if HF token is available")
```

The `_run_pipeline()` function (line 243) accepts `hf_token` and `no_diarize` kwargs and runs diarization when:
- `no_diarize` is False AND
- A HuggingFace token is found (via `get_hf_token()`)

On diarization failure, it gracefully continues without speakers (line 267: `except Exception as e`).

The full `_run_pipeline` function:

```python
def _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=None, no_diarize=False):
    """Transcribe audio, optionally diarize, and optionally analyze with Claude."""
    from listener.transcriber import transcribe

    click.echo("--- Transcription ---\n")
    result = transcribe(audio_path, model_size=model_size, language=language)

    if not result.segments:
        click.echo("No speech detected in the audio.")
        return

    # Speaker diarization
    if not no_diarize:
        from listener.diarizer import get_hf_token
        token = get_hf_token(cli_token=hf_token)
        if token:
            click.echo("\n--- Speaker Diarization ---\n")
            try:
                from listener.diarizer import diarize, align_speakers
                diarization = diarize(audio_path, hf_token=token)
                result.segments = align_speakers(diarization, result.segments)
                click.echo(f"Speakers assigned to {len(result.segments)} segments.\n")
            except Exception as e:
                click.echo(f"Diarization failed (continuing without speakers): {e}", err=True)
        else:
            click.echo("\nNo HuggingFace token found — skipping speaker diarization.")
            click.echo("Set HF_TOKEN env var or use --hf-token to enable.\n")

    transcript_text = result.to_timestamped_text()
    # ... rest of pipeline continues unchanged
```

- **Verification:**

```bash
python -m listener record --help | grep -E "hf-token|no-diarize"
python -m listener transcribe --help | grep -E "hf-token|no-diarize"
```

---

### Task 4: Web backend -- diarization in processing pipeline

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** Modify (ALREADY DONE)
- **Details:**

**4a. Diarization step in `_process_recording()` (lines 240-250):**

```python
# Speaker diarization (if HF token is available)
try:
    from listener.diarizer import get_hf_token, diarize, align_speakers
    hf_token = get_hf_token()
    if hf_token:
        with _lock:
            _state["step"] = "diarizing"
        diarization = diarize(audio_path, hf_token=hf_token)
        result.segments = align_speakers(diarization, result.segments)
        logger.info("Speaker diarization complete for session %s", session_id)
except Exception as e:
    logger.warning("Diarization failed (continuing without): %s", e)
```

**4b. Speaker metadata in meta.json (lines 282-298):**

```python
speaker_info = {}
if result.has_speakers:
    from listener.diarizer import compute_talk_times
    talk_times = compute_talk_times(result.segments)
    speaker_info = {
        label: {"talk_time_seconds": round(secs, 1)}
        for label, secs in sorted(talk_times.items())
    }

meta = {
    "title": title,
    "language": result.language,
    "language_probability": result.language_probability,
    "duration": result.duration,
    "speakers": speaker_info,
}
```

**4c. `/api/speakers/<session_id>` endpoint (lines 206-216):**

```python
@app.route("/api/speakers/<session_id>")
def api_speakers(session_id):
    """Return speaker info for a session from its meta.json."""
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    if not meta_path.exists():
        return jsonify({"speakers": {}})
    try:
        meta = json.loads(meta_path.read_text())
        return jsonify({"speakers": meta.get("speakers", {})})
    except Exception:
        return jsonify({"speakers": {}})
```

- **Verification:**

```bash
python -c "from listener.web.app import app; print([r.rule for r in app.url_map.iter_rules() if 'speaker' in r.rule])"
```

Expected: `['/api/speakers/<session_id>']`

---

### Task 5: Frontend -- speaker colors, legend, and rendering

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify (ALREADY DONE)
- **Details:**

**5a. Speaker color CSS classes (lines 145-173):**

8 speaker colors (`.spk-1` through `.spk-8`) plus `.spk-unknown`. Each has a text color and light background. Corresponding legend dot colors (`.legend-dot.c1` through `.c8`, `.cu`).

```css
.spk{font-weight:700;padding:1px 6px;border-radius:4px;font-size:12px;margin-right:2px}
.spk-1{color:#6366f1;background:#eef2ff}
.spk-2{color:#059669;background:#ecfdf5}
.spk-3{color:#d97706;background:#fffbeb}
.spk-4{color:#dc2626;background:#fef2f2}
.spk-5{color:#7c3aed;background:#f5f3ff}
.spk-6{color:#0891b2;background:#ecfeff}
.spk-7{color:#be185d;background:#fdf2f8}
.spk-8{color:#4338ca;background:#eef2ff}
.spk-unknown{color:#64748b;background:#f1f5f9}
```

**5b. Speaker legend container CSS (lines 156-172):**

```css
.speaker-legend{
  display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;
  padding:10px 14px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;
}
.legend-item{display:flex;align-items:center;gap:5px;font-size:12px;color:#475569}
.legend-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.talk-time{font-size:11px;color:#94a3b8;margin-left:2px}
```

**5c. Inline rendering -- `il()` function updated (lines 543-559):**

The `il()` function detects speaker labels in bold markdown like `**[00:15] Speaker 1:**` and renders them with colored `.spk` spans and `.ts` timestamp spans.

```javascript
function il(s){
  return s
    .replace(/\*\*(.*?)\*\*/g, function(m, inner) {
      var spkMatch = inner.match(/^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+(Speaker\s+(\d+)|Unknown):/);
      if (spkMatch) {
        var ts = spkMatch[1];
        var speaker = spkMatch[2];
        var num = spkMatch[3];
        var cls = num ? 'spk-' + Math.min(parseInt(num), 8) : 'spk-unknown';
        return '<span class="ts">[' + ts + ']</span> <span class="spk ' + cls + '">' + speaker + ':</span>';
      }
      return '<strong>' + inner + '</strong>';
    })
    .replace(/\*(.*?)\*/g,'<em>$1</em>')
    .replace(/`(.*?)`/g,'<code>$1</code>')
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,'<span class="ts">[$1]</span>');
}
```

**5d. Speaker legend loader -- `loadSpeakerLegend()` function (lines 565-585):**

```javascript
async function loadSpeakerLegend(sessionId) {
  try {
    const r = await f(`/api/speakers/${sessionId}`);
    const data = await r.json();
    const speakers = data.speakers || {};
    const keys = Object.keys(speakers);
    if (keys.length === 0) return '';

    let items = '';
    keys.forEach((label, i) => {
      const num = label.match(/Speaker\s+(\d+)/);
      const cls = num ? 'c' + Math.min(parseInt(num[1]), 8) : 'cu';
      const secs = speakers[label].talk_time_seconds || 0;
      const timeStr = fmtDur(secs);
      items += `<div class="legend-item"><span class="legend-dot ${cls}"></span>${esc(label)}<span class="talk-time">${timeStr}</span></div>`;
    });
    return `<div class="speaker-legend">${items}</div>`;
  } catch (e) {
    return '';
  }
}
```

**5e. `loadTab()` calls `loadSpeakerLegend()` for transcript tab (lines 482-486):**

```javascript
let legendHtml = '';
if (tab === 'transcript' && viewerSession?.id) {
  legendHtml = await loadSpeakerLegend(viewerSession.id);
}
c.innerHTML = legendHtml + renderMd(md);
```

**5f. Poll function maps `diarizing` step (line 402):**

```javascript
const map={transcribing:'Transcribing audio...',diarizing:'Identifying speakers...',titling:'Generating title...',analyzing:'Analyzing with Claude...'};
```

---

## Final Verification

Run all these commands to confirm F1 is fully working:

```bash
# 1. Imports work
python -c "from listener.diarizer import get_hf_token, load_pipeline, diarize, align_speakers, compute_talk_times; print('OK: diarizer imports')"

# 2. Segment has speaker field
python -c "from listener.transcriber import Segment; s = Segment(0, 1, 'test', 'Speaker 1'); print(f'OK: speaker={s.speaker}')"

# 3. TranscriptionResult.has_speakers works
python -c "from listener.transcriber import Segment, TranscriptionResult; r = TranscriptionResult([Segment(0,1,'hi','Speaker 1')],'en',0.99,60); print(f'OK: has_speakers={r.has_speakers}')"

# 4. CLI flags present
python -m listener record --help 2>&1 | grep -q "hf-token" && echo "OK: --hf-token flag" || echo "MISSING: --hf-token"
python -m listener record --help 2>&1 | grep -q "no-diarize" && echo "OK: --no-diarize flag" || echo "MISSING: --no-diarize"

# 5. Flask endpoint exists
python -c "from listener.web.app import app; rules = [r.rule for r in app.url_map.iter_rules()]; assert '/api/speakers/<session_id>' in rules; print('OK: /api/speakers endpoint')"

# 6. Web app starts (quick check)
python -c "from listener.web.app import app; client = app.test_client(); r = client.get('/api/speakers/test'); print(f'OK: speakers endpoint returns {r.status_code}')"

# 7. Dependencies in pyproject.toml
grep -q "pyannote.audio" /Users/alperencngzz/Desktop/listener/pyproject.toml && echo "OK: pyannote.audio dep" || echo "MISSING"
grep -q "torch" /Users/alperencngzz/Desktop/listener/pyproject.toml && echo "OK: torch dep" || echo "MISSING"
```

### End-to-end test (requires HF token and audio file):

```bash
# With a real audio file:
HF_TOKEN=hf_your_token listener transcribe /path/to/meeting.wav --language en

# Verify the output transcript has speaker labels:
grep -c "Speaker" transcripts/*_transcript.md
```

---

## Notes for Downstream Features

- **F8 (Meeting Analytics Dashboard)** depends on F1's `compute_talk_times()` function and the `speakers` field in `meta.json`. Both are available.
- **F2 (Chat with Transcript)** benefits from speaker labels in transcript text but does not require F1.
- The speaker alignment uses temporal overlap (not word-level), which is simpler but sufficient for segment-level attribution.
- The pipeline gracefully degrades: if no HF token is set or diarization fails, transcription proceeds without speaker labels.
- The `speakers` dict in `meta.json` follows this schema and can be extended later:
  ```json
  {
    "speakers": {
      "Speaker 1": {"talk_time_seconds": 1245.3},
      "Speaker 2": {"talk_time_seconds": 890.7}
    }
  }
  ```
- The diarization pipeline is cached in the module-level `_pipeline` variable, so subsequent calls in the same process reuse the loaded model.
