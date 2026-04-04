# Implementation Plan: F1 — Speaker Diarization

## Pre-Implementation Checklist

**Files the implementor MUST read fresh before starting** (they may have been modified by previous features):
- `/Users/alperencngzz/Desktop/listener/listener/transcriber.py`
- `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- `/Users/alperencngzz/Desktop/listener/listener/analyzer.py`

**Things to verify before starting:**
- `pyannote.audio` is NOT yet in `pyproject.toml` — it needs to be added
- No previous features have been implemented (progress.md is empty)
- The `listener/diarizer.py` file does NOT exist yet — it needs to be created

## Dependencies

### Packages to add to `pyproject.toml`

Add these to the `dependencies` list:
```
"pyannote.audio>=3.1",
"torch>=2.0.0",
```

### External Requirements

The user needs a **HuggingFace token** with read access. They must:
1. Create account at https://huggingface.co
2. Generate a read token at https://huggingface.co/settings/tokens
3. Accept license conditions at https://huggingface.co/pyannote/speaker-diarization-3.1
4. Accept license conditions at https://huggingface.co/pyannote/segmentation-3.0

Token is provided via (checked in this order):
1. CLI flag: `--hf-token hf_xxx`
2. Environment variable: `HF_TOKEN` or `HUGGINGFACE_TOKEN`
3. Config file: `~/.listener/config.yaml` with `hf_token: hf_xxx`

---

## Implementation Tasks

### Task 1: Add dependencies to pyproject.toml

- **File:** `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- **Action:** Modify
- **Details:** Add `pyannote.audio` and `torch` to the dependencies list. Find the existing `dependencies` array and add two entries:

```toml
dependencies = [
    "sounddevice>=0.4.6",
    "soundfile>=0.12.1",
    "numpy>=1.24.0",
    "faster-whisper>=1.0.0",
    "claude-code-sdk>=0.0.25",
    "jsonschema>=4.0.0",
    "click>=8.0.0",
    "flask>=3.0.0",
    "pyannote.audio>=3.1",
    "torch>=2.0.0",
]
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "import toml; print('ok')" 2>/dev/null || echo "just check file visually"`

### Task 2: Update Segment dataclass in transcriber.py

- **File:** `/Users/alperencngzz/Desktop/listener/listener/transcriber.py`
- **Action:** Modify
- **Details:**

**2a.** Add `speaker` field to the `Segment` dataclass. Change:
```python
@dataclass
class Segment:
    """A transcribed speech segment."""
    start: float
    end: float
    text: str
```
To:
```python
@dataclass
class Segment:
    """A transcribed speech segment."""
    start: float
    end: float
    text: str
    speaker: str = ""
```

**2b.** Add a property to `TranscriptionResult` to check if diarization data is present:
```python
@property
def has_speakers(self) -> bool:
    return any(s.speaker for s in self.segments)
```

Add this right after the existing `full_text` property.

**2c.** Update the `to_timestamped_text()` method to include speaker labels when available. Replace:
```python
    def to_timestamped_text(self) -> str:
        """Format as timestamped transcript lines."""
        lines = []
        for seg in self.segments:
            ts = _fmt_ts(seg.start)
            lines.append(f"[{ts}] {seg.text}")
        return "\n\n".join(lines)
```
With:
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

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.transcriber import Segment; s = Segment(0, 1, 'hi', 'Speaker 1'); print(s.speaker)"`

### Task 3: Create the diarizer module

- **File:** `/Users/alperencngzz/Desktop/listener/listener/diarizer.py`
- **Action:** Create
- **Details:** Create this complete file:

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

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.diarizer import get_hf_token, align_speakers; print('diarizer module imports OK')"`

### Task 4: Modify the CLI to add diarization flags

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** Modify
- **Details:**

**4a.** Add `--hf-token` and `--no-diarize` options to the `record_cmd` function. Find this block:
```python
@cli.command("record")
@click.option("--device", "-d", type=int, default=None,
              help="Audio input device ID (run 'listener devices' to list)")
@click.option("--language", "-l", default=None,
              help="Transcription language: en, tr, or omit for auto-detect")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size (default: large-v3)")
@click.option("--no-analyze", is_flag=True,
              help="Skip Claude analysis, only transcribe")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory (default: ./transcripts)")
def record_cmd(device, language, model_size, no_analyze, output_dir):
```
Replace with:
```python
@cli.command("record")
@click.option("--device", "-d", type=int, default=None,
              help="Audio input device ID (run 'listener devices' to list)")
@click.option("--language", "-l", default=None,
              help="Transcription language: en, tr, or omit for auto-detect")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size (default: large-v3)")
@click.option("--no-analyze", is_flag=True,
              help="Skip Claude analysis, only transcribe")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory (default: ./transcripts)")
@click.option("--hf-token", default=None,
              help="HuggingFace token for speaker diarization (or set HF_TOKEN env var)")
@click.option("--no-diarize", is_flag=True,
              help="Skip speaker diarization even if HF token is available")
def record_cmd(device, language, model_size, no_analyze, output_dir, hf_token, no_diarize):
```

**4b.** Update the call to `_run_pipeline` at the end of `record_cmd`. Find:
```python
    _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp)
```
Replace with:
```python
    _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=hf_token, no_diarize=no_diarize)
```

**4c.** Add same flags to `transcribe_cmd`. Find:
```python
@cli.command("transcribe")
@click.argument("audio_file", type=click.Path(exists=True))
@click.option("--language", "-l", default=None,
              help="Language: en, tr, or omit for auto")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size")
@click.option("--no-analyze", is_flag=True, help="Skip Claude analysis")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory")
def transcribe_cmd(audio_file, language, model_size, no_analyze, output_dir):
    """Transcribe an existing audio file."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    _run_pipeline(audio_file, language, model_size, no_analyze, output_dir, timestamp)
```
Replace with:
```python
@cli.command("transcribe")
@click.argument("audio_file", type=click.Path(exists=True))
@click.option("--language", "-l", default=None,
              help="Language: en, tr, or omit for auto")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size")
@click.option("--no-analyze", is_flag=True, help="Skip Claude analysis")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory")
@click.option("--hf-token", default=None,
              help="HuggingFace token for speaker diarization (or set HF_TOKEN env var)")
@click.option("--no-diarize", is_flag=True,
              help="Skip speaker diarization even if HF token is available")
def transcribe_cmd(audio_file, language, model_size, no_analyze, output_dir, hf_token, no_diarize):
    """Transcribe an existing audio file."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    _run_pipeline(audio_file, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=hf_token, no_diarize=no_diarize)
```

**4d.** Update `_run_pipeline` function signature and add diarization step. Replace the ENTIRE `_run_pipeline` function:

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
    duration_str = _fmt_duration(result.duration)
    dt = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
    date_display = dt.strftime("%Y-%m-%d %H:%M")

    # Save transcript
    transcript_md = (
        f"# Meeting Transcript -- {date_display}\n\n"
        f"**Duration:** {duration_str}  \n"
        f"**Language:** {result.language} "
        f"({result.language_probability:.0%} confidence)\n\n"
        f"---\n\n"
        f"{transcript_text}\n"
    )
    transcript_path = Path(output_dir) / f"{timestamp}_transcript.md"
    transcript_path.write_text(transcript_md)
    click.echo(f"\nTranscript saved: {transcript_path}")

    if no_analyze:
        return

    # Analyze
    click.echo("\n--- Analysis ---\n")
    click.echo("Analyzing with Claude...")

    from listener.analyzer import analyze_transcript_sync

    try:
        analysis = analyze_transcript_sync(transcript_text)

        analysis_md = (
            f"# Meeting Analysis -- {date_display}\n\n"
            f"**Duration:** {duration_str}  \n"
            f"**Language:** {result.language}\n\n"
            f"---\n\n"
            f"{analysis}\n\n"
            f"---\n\n"
            f"# Full Transcript\n\n"
            f"{transcript_text}\n"
        )
        analysis_path = Path(output_dir) / f"{timestamp}_analysis.md"
        analysis_path.write_text(analysis_md)
        click.echo(f"Analysis saved: {analysis_path}")

    except Exception as e:
        click.echo(f"\nAnalysis failed: {e}", err=True)
        click.echo("Transcript was saved successfully. Re-run with:")
        click.echo(f"  listener analyze {transcript_path}")
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.cli import cli; print('CLI imports OK')"`

### Task 5: Add diarization step to the web backend

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** Modify
- **Details:**

**5a.** Add `"diarizing"` to the processing pipeline and HF token support. Find and replace the `_process_recording` function. Replace the ENTIRE function (from `def _process_recording` to the end of its `except` block):

```python
def _process_recording(audio_path, session_id, language, model_size, skip_analysis):
    try:
        from listener.transcriber import transcribe

        with _lock:
            _state["step"] = "transcribing"

        result = transcribe(audio_path, model_size=model_size, language=language)

        if not result.segments:
            with _lock:
                _state["status"] = "error"
                _state["error"] = "No speech detected in the recording"
                _state["step"] = None
            return

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

        # Save transcript
        transcript_text = result.to_timestamped_text()
        duration_str = _fmt_duration(result.duration)
        dt = datetime.strptime(session_id, "%Y-%m-%d_%H-%M-%S")
        date_display = dt.strftime("%Y-%m-%d %H:%M")

        transcript_md = (
            f"# Meeting Transcript -- {date_display}\n\n"
            f"**Duration:** {duration_str}  \n"
            f"**Language:** {result.language} "
            f"({result.language_probability:.0%} confidence)\n\n"
            f"---\n\n"
            f"{transcript_text}\n"
        )
        transcript_filename = f"{session_id}_transcript.md"
        (OUTPUT_DIR / transcript_filename).write_text(transcript_md)

        files = {"transcript": transcript_filename, "audio": f"{session_id}.wav"}

        # Generate title
        title = f"Meeting {date_display}"
        try:
            with _lock:
                _state["step"] = "titling"
            from listener.analyzer import generate_title_sync
            title = generate_title_sync(transcript_text)
        except Exception as e:
            logger.warning("Title generation failed: %s", e)

        # Save metadata (include speaker info)
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
        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(json.dumps(meta, ensure_ascii=False))

        # Analysis
        if not skip_analysis:
            with _lock:
                _state["step"] = "analyzing"

            from listener.analyzer import analyze_transcript_sync

            analysis = analyze_transcript_sync(transcript_text)

            analysis_md = (
                f"# Meeting Analysis -- {date_display}\n\n"
                f"**Duration:** {duration_str}  \n"
                f"**Language:** {result.language}\n\n"
                f"---\n\n"
                f"{analysis}\n\n"
                f"---\n\n"
                f"# Full Transcript\n\n"
                f"{transcript_text}\n"
            )
            analysis_filename = f"{session_id}_analysis.md"
            (OUTPUT_DIR / analysis_filename).write_text(analysis_md)
            files["analysis"] = analysis_filename

        with _lock:
            _state["status"] = "done"
            _state["step"] = None
            _state["files"] = files
            _state["title"] = title

    except Exception as e:
        with _lock:
            _state["status"] = "error"
            _state["error"] = str(e)
            _state["step"] = None
```

**5b.** Add a new API endpoint to return speaker metadata for a session. Add this BEFORE the `# Background processing` comment block:

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

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('Web app imports OK')"`

### Task 6: Update the frontend for speaker color-coding

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:**

**6a.** Add speaker color CSS. Find this line in the `<style>` block:
```css
.content audio{width:100%;margin:12px 0}
```
Add AFTER it:
```css

/* ---- Speaker colors ---- */
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

.speaker-legend{
  display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;
  padding:10px 14px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;
}
.legend-item{display:flex;align-items:center;gap:5px;font-size:12px;color:#475569}
.legend-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.legend-dot.c1{background:#6366f1}
.legend-dot.c2{background:#059669}
.legend-dot.c3{background:#d97706}
.legend-dot.c4{background:#dc2626}
.legend-dot.c5{background:#7c3aed}
.legend-dot.c6{background:#0891b2}
.legend-dot.c7{background:#be185d}
.legend-dot.c8{background:#4338ca}
.legend-dot.cu{background:#64748b}

.talk-time{font-size:11px;color:#94a3b8;margin-left:2px}
```

**6b.** Update the poll function to handle the "diarizing" step. Find this line in the `poll()` function:
```javascript
      const map={transcribing:'Transcribing audio...',titling:'Generating title...',analyzing:'Analyzing with Claude...'};
```
Replace with:
```javascript
      const map={transcribing:'Transcribing audio...',diarizing:'Identifying speakers...',titling:'Generating title...',analyzing:'Analyzing with Claude...'};
```

**6c.** Update the `il()` inline formatting function to add speaker color classes. Replace the entire `il` function:
```javascript
function il(s){
  return s
    .replace(/\*\*(.*?)\*\*/g, function(m, inner) {
      // Check if this is a speaker label like "[00:15] Speaker 1:"
      const spkMatch = inner.match(/^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+(Speaker\s+(\d+)|Unknown):/);
      if (spkMatch) {
        const ts = spkMatch[1];
        const speaker = spkMatch[2];
        const num = spkMatch[3];
        const cls = num ? 'spk-' + Math.min(parseInt(num), 8) : 'spk-unknown';
        return '<span class="ts">[' + ts + ']</span> <span class="spk ' + cls + '">' + speaker + ':</span>';
      }
      return '<strong>' + inner + '</strong>';
    })
    .replace(/\*(.*?)\*/g,'<em>$1</em>')
    .replace(/`(.*?)`/g,'<code>$1</code>')
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,'<span class="ts">[$1]</span>');
}
```

**6d.** Add a speaker legend that loads when viewing a transcript with diarization. Add this new function right BEFORE the `// Sessions list` comment:

```javascript
// ===========================================================================
// Speaker Legend
// ===========================================================================
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

**6e.** Update the `loadTab` function to insert the speaker legend above the transcript content. Replace the ENTIRE `loadTab` function:

```javascript
async function loadTab(tab){
  const c=Q('#content');
  const files=viewerSession?.files||{};

  if(tab==='audio'){
    c.innerHTML=`<audio controls src="/api/download/${files.audio}" style="width:100%"></audio>
      <p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:8px">Use the player above to listen to the recording</p>`;
    return;
  }

  const fname=tab==='transcript'?files.transcript:files.analysis;
  if(!fname){c.innerHTML='<p class="empty">Not available</p>';return}

  c.innerHTML='<p style="color:#94a3b8">Loading...</p>';
  try{
    const r=await f(`/api/view/${fname}`);
    const md=await r.text();
    let legendHtml = '';
    if (tab === 'transcript' && viewerSession?.id) {
      legendHtml = await loadSpeakerLegend(viewerSession.id);
    }
    c.innerHTML = legendHtml + renderMd(md);
  }catch(e){
    c.innerHTML='<p class="empty">Failed to load</p>';
  }
}
```

- **Verification:** Open `http://127.0.0.1:8642` after running `listener web` and verify the page loads without JS errors.

---

## Summary of All File Changes

| File | Action | What Changes |
|---|---|---|
| `/Users/alperencngzz/Desktop/listener/pyproject.toml` | Modify | Add `pyannote.audio>=3.1` and `torch>=2.0.0` dependencies |
| `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` | Modify | Add `speaker` field to `Segment`, add `has_speakers` property, update `to_timestamped_text()` to include speaker labels |
| `/Users/alperencngzz/Desktop/listener/listener/diarizer.py` | **Create** | New module: HF token resolution, pipeline loading/caching, diarization, alignment, talk-time computation |
| `/Users/alperencngzz/Desktop/listener/listener/cli.py` | Modify | Add `--hf-token` and `--no-diarize` flags to `record` and `transcribe` commands; update `_run_pipeline` with diarization step |
| `/Users/alperencngzz/Desktop/listener/listener/web/app.py` | Modify | Add diarization step in `_process_recording`, add `"diarizing"` processing state, add `/api/speakers/<session_id>` endpoint, save speaker info in meta.json |
| `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` | Modify | Add speaker color CSS classes, update `il()` for speaker label rendering, add speaker legend component, update `loadTab` and `poll` |

---

## Final Verification

Run these commands in order:

1. **Check imports work:**
   ```bash
   cd /Users/alperencngzz/Desktop/listener
   python -c "from listener.transcriber import Segment; s = Segment(0, 1, 'test', 'Speaker 1'); print(f'speaker={s.speaker}')"
   ```
   Expected: `speaker=Speaker 1`

2. **Check diarizer module loads:**
   ```bash
   python -c "from listener.diarizer import get_hf_token, align_speakers, compute_talk_times; print('All diarizer functions import OK')"
   ```
   Expected: `All diarizer functions import OK`

3. **Check CLI has new flags:**
   ```bash
   python -m listener record --help
   ```
   Expected: Should show `--hf-token` and `--no-diarize` options in the help output.

4. **Check web app starts:**
   ```bash
   python -c "from listener.web.app import app; print('Web app OK')"
   ```
   Expected: `Web app OK`

5. **Check transcript formatting with speakers:**
   ```bash
   python -c "
   from listener.transcriber import Segment, TranscriptionResult
   segs = [
       Segment(0.0, 3.5, 'Hello everyone.', 'Speaker 1'),
       Segment(3.8, 7.2, 'Thanks for having us.', 'Speaker 2'),
       Segment(7.5, 12.0, 'No speaker here.', ''),
   ]
   r = TranscriptionResult(segments=segs, language='en', language_probability=0.98, duration=12.0)
   print(r.to_timestamped_text())
   print()
   print('has_speakers:', r.has_speakers)
   "
   ```
   Expected output:
   ```
   **[00:00] Speaker 1:** Hello everyone.

   **[00:03] Speaker 2:** Thanks for having us.

   [00:07] No speaker here.

   has_speakers: True
   ```

6. **Full end-to-end test** (requires HF token and audio file):
   ```bash
   HF_TOKEN=hf_your_token_here python -m listener transcribe /path/to/test.wav --output-dir ./test_output
   ```
   Expected: Transcript with speaker labels saved to `./test_output/`.

---

## Notes for Future Features

- **F8 (Meeting Analytics Dashboard)** depends on F1 — it uses `compute_talk_times()` from `diarizer.py` and the `speakers` field in `meta.json` to show talk-time distribution charts.
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
- If `pyannote.audio` fails to install (e.g., architecture issues), the rest of the app still works — diarization is wrapped in try/except everywhere.
