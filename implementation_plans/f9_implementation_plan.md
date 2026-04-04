# Implementation Plan: F9 — Real-Time Live Transcription

## Overview

Add real-time streaming transcription to Listener. While the user records a meeting, partial transcript text appears live in the browser. The approach: keep the existing server-side `sounddevice` recording, but also feed audio chunks to faster-whisper in a background thread. Push partial results to the browser via **Server-Sent Events (SSE)** — no new dependencies needed.

The UI shows two regions: **finalized** text (confirmed segments) and **tentative** text (current chunk, may change). When recording stops, the standard post-processing pipeline runs as before.

---

## Pre-Implementation Checklist

Files the implementor **must read fresh** before starting (they may have been modified by previous features):

| File | Why |
|---|---|
| `/Users/alperencngzz/Desktop/listener/listener/web/app.py` | Flask backend — all features modify this. Currently ~467 lines. |
| `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` | Frontend — single file with embedded CSS/JS. Currently ~985 lines. |
| `/Users/alperencngzz/Desktop/listener/listener/recorder.py` | Audio recording module — must understand `Recorder` class and `_audio_callback`. |
| `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` | Whisper transcription — `Segment`, `TranscriptionResult`, `transcribe()` function. |
| `/Users/alperencngzz/Desktop/listener/pyproject.toml` | Dependencies — check current state. |

Things to verify:
- `flask` is already in dependencies (yes, `flask>=3.0.0`)
- `faster-whisper>=1.0.0` is already present (yes)
- `numpy>=1.24.0` is already present (yes)
- `sounddevice>=0.4.6` is already present (yes)
- No WebSocket library is needed — we use Flask's native SSE via `Response(stream_with_context(generate()))`

---

## Dependencies

**No new pip dependencies required.** This feature uses:
- `faster-whisper` (already installed) — for chunked Whisper inference
- `flask` (already installed) — SSE via `Response` with `stream_with_context`
- `numpy` (already installed) — audio buffer manipulation
- `threading`, `queue`, `collections.deque` — all stdlib

---

## Architecture Design

```
Browser                          Flask Server
  |                                  |
  |--- POST /api/start ------------->|  Start recording (sounddevice)
  |    {live_transcription: true}    |  + Start StreamingTranscriber thread
  |                                  |
  |--- GET /api/live-stream -------->|  SSE connection opened
  |                                  |
  |<--- SSE: partial results --------|  Every ~3-5 seconds:
  |    {type: "tentative",           |    Whisper processes audio window
  |     text: "..."}                 |    Sends finalized + tentative text
  |<--- SSE: partial results --------|
  |    {type: "finalized",           |
  |     segments: [...]}             |
  |                                  |
  |--- POST /api/stop -------------->|  Stop recording
  |                                  |  StreamingTranscriber stops
  |<--- SSE: {type: "done"} ---------|  SSE stream closes
  |                                  |  Standard pipeline runs (diarize, analyze, etc.)
```

**Key design decisions:**
1. **SSE over WebSocket** — No new dependency, simpler, one-directional (server→client) is sufficient since audio is recorded server-side.
2. **5-second windows with 2-second overlap** — Balances latency vs accuracy. Overlap prevents word-splitting at boundaries.
3. **Model preloaded** — The WhisperModel stays in memory during the entire recording session for low latency.
4. **Tentative vs Finalized** — The latest chunk's output is "tentative" (may be revised). Previous chunks are "finalized" (locked in). This matches how commercial tools like Otter.ai work.
5. **VAD filtering** — Use faster-whisper's built-in VAD to skip silent chunks.

---

## Implementation Tasks

### Task 1: Create `listener/streaming.py` — Streaming Transcription Engine

- **File:** `/Users/alperencngzz/Desktop/listener/listener/streaming.py`
- **Action:** CREATE
- **Details:**

```python
"""Real-time streaming transcription using faster-whisper.

Processes audio in overlapping windows and yields partial transcript
results as segments are recognized. Designed to run alongside the
Recorder, consuming audio chunks from a shared queue.
"""

import io
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WINDOW_SECONDS = 5          # Whisper processes this much audio at a time
OVERLAP_SECONDS = 2         # Overlap between consecutive windows
STEP_SECONDS = WINDOW_SECONDS - OVERLAP_SECONDS  # 3 seconds of new audio per step
MIN_AUDIO_SECONDS = 1.5     # Minimum audio to bother transcribing
SAMPLE_RATE = 16000          # Whisper expects 16kHz mono


@dataclass
class LiveSegment:
    """A single live transcript segment."""
    start: float
    end: float
    text: str
    is_final: bool = False


@dataclass
class LiveUpdate:
    """An update pushed to the client via SSE."""
    finalized_segments: list[LiveSegment] = field(default_factory=list)
    tentative_text: str = ""
    elapsed: float = 0.0
    done: bool = False
    error: str = ""


class StreamingTranscriber:
    """Processes audio chunks in real-time and produces transcript updates.

    Usage:
        st = StreamingTranscriber(model_size="large-v3", language=None)
        st.start()

        # In audio callback:
        st.feed_audio(numpy_chunk, sample_rate=48000)

        # In SSE endpoint:
        for update in st.updates():
            yield format_sse(update)

        # When recording stops:
        st.stop()
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        language: str | None = None,
        device: str = "cpu",
        compute_type: str = "auto",
    ):
        self.model_size = model_size
        self.language = language
        self.device = device
        self.compute_type = compute_type

        self._model = None
        self._audio_buffer = deque()  # list of numpy arrays (16kHz mono float32)
        self._buffer_lock = threading.Lock()
        self._total_samples = 0       # total samples fed so far

        self._finalized: list[LiveSegment] = []  # confirmed segments
        self._tentative_text: str = ""            # latest tentative text
        self._update_queue: queue.Queue[LiveUpdate] = queue.Queue(maxsize=100)

        self._running = False
        self._thread: threading.Thread | None = None
        self._start_time: float = 0
        self._last_processed_samples = 0  # how far we've finalized

    def start(self) -> None:
        """Load model and start the processing thread."""
        if self._running:
            return

        logger.info("Loading Whisper %s model for streaming...", self.model_size)
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Whisper model loaded for streaming")

        self._running = True
        self._start_time = time.time()
        self._finalized = []
        self._tentative_text = ""
        self._total_samples = 0
        self._last_processed_samples = 0

        # Clear queues
        while not self._update_queue.empty():
            try:
                self._update_queue.get_nowait()
            except queue.Empty:
                break

        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the processing thread. Processes any remaining audio first."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=30)
            self._thread = None

        # Send final done update
        update = LiveUpdate(
            finalized_segments=list(self._finalized),
            tentative_text="",
            elapsed=time.time() - self._start_time if self._start_time else 0,
            done=True,
        )
        try:
            self._update_queue.put_nowait(update)
        except queue.Full:
            pass

    def feed_audio(self, data: np.ndarray, sample_rate: int) -> None:
        """Feed audio data from the recorder's callback.

        Resamples to 16kHz mono float32 if needed.
        """
        # Convert to float32 if needed
        if data.dtype != np.float32:
            audio = data.astype(np.float32)
        else:
            audio = data.copy()

        # Convert to mono if stereo
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        # Resample to 16kHz if needed
        if sample_rate != SAMPLE_RATE:
            # Simple decimation — good enough for speech
            ratio = sample_rate / SAMPLE_RATE
            indices = np.arange(0, len(audio), ratio).astype(int)
            indices = indices[indices < len(audio)]
            audio = audio[indices]

        with self._buffer_lock:
            self._audio_buffer.append(audio)
            self._total_samples += len(audio)

    def updates(self):
        """Generator that yields LiveUpdate objects. Used by the SSE endpoint."""
        while True:
            try:
                update = self._update_queue.get(timeout=1.0)
                yield update
                if update.done:
                    return
            except queue.Empty:
                if not self._running:
                    # Final yield
                    yield LiveUpdate(
                        finalized_segments=list(self._finalized),
                        tentative_text="",
                        elapsed=time.time() - self._start_time if self._start_time else 0,
                        done=True,
                    )
                    return
                # Yield a heartbeat to keep SSE alive
                yield LiveUpdate(
                    finalized_segments=list(self._finalized),
                    tentative_text=self._tentative_text,
                    elapsed=time.time() - self._start_time if self._start_time else 0,
                )

    def get_finalized_segments(self) -> list[LiveSegment]:
        """Return all finalized segments (for saving after recording stops)."""
        return list(self._finalized)

    def _get_audio_array(self) -> np.ndarray:
        """Concatenate all buffered audio into a single array."""
        with self._buffer_lock:
            if not self._audio_buffer:
                return np.array([], dtype=np.float32)
            result = np.concatenate(list(self._audio_buffer))
        return result

    def _process_loop(self) -> None:
        """Main processing loop — runs in a background thread."""
        window_samples = int(WINDOW_SECONDS * SAMPLE_RATE)
        step_samples = int(STEP_SECONDS * SAMPLE_RATE)
        min_samples = int(MIN_AUDIO_SECONDS * SAMPLE_RATE)

        while self._running:
            total = self._total_samples
            # Wait until we have enough new audio for a step
            new_samples = total - self._last_processed_samples
            if new_samples < step_samples:
                time.sleep(0.5)
                continue

            audio = self._get_audio_array()
            if len(audio) < min_samples:
                time.sleep(0.5)
                continue

            # Determine the window to process
            # We process the last WINDOW_SECONDS of audio
            if len(audio) > window_samples:
                window_start_sample = max(0, len(audio) - window_samples)
                window_audio = audio[window_start_sample:]
                window_start_time = window_start_sample / SAMPLE_RATE
            else:
                window_audio = audio
                window_start_time = 0.0

            try:
                segments = self._transcribe_chunk(window_audio, window_start_time)
            except Exception as e:
                logger.warning("Streaming transcription error: %s", e)
                time.sleep(1)
                continue

            # Split into finalized and tentative
            # Everything before the overlap region is finalized
            # The last OVERLAP_SECONDS of output is tentative
            total_time = len(audio) / SAMPLE_RATE
            finalize_cutoff = total_time - OVERLAP_SECONDS - 1.0  # 1s extra margin

            new_finalized = []
            tentative_parts = []

            for seg in segments:
                if seg.end <= finalize_cutoff and finalize_cutoff > 0:
                    # Check it doesn't duplicate existing finalized segments
                    if not self._is_duplicate(seg):
                        seg.is_final = True
                        new_finalized.append(seg)
                else:
                    tentative_parts.append(seg.text)

            self._finalized.extend(new_finalized)
            self._tentative_text = " ".join(tentative_parts)

            # Mark how far we've processed
            self._last_processed_samples = total

            # Push update
            update = LiveUpdate(
                finalized_segments=list(self._finalized),
                tentative_text=self._tentative_text,
                elapsed=time.time() - self._start_time,
            )
            try:
                self._update_queue.put_nowait(update)
            except queue.Full:
                # Drop oldest update if queue is full
                try:
                    self._update_queue.get_nowait()
                    self._update_queue.put_nowait(update)
                except queue.Empty:
                    pass

        # Process any remaining audio one final time
        audio = self._get_audio_array()
        if len(audio) > int(MIN_AUDIO_SECONDS * SAMPLE_RATE):
            try:
                segments = self._transcribe_chunk(audio, 0.0)
                for seg in segments:
                    if not self._is_duplicate(seg):
                        seg.is_final = True
                        self._finalized.append(seg)
                self._tentative_text = ""
            except Exception as e:
                logger.warning("Final streaming transcription error: %s", e)

    def _transcribe_chunk(self, audio: np.ndarray, offset: float) -> list[LiveSegment]:
        """Run Whisper on an audio chunk. Returns LiveSegment list."""
        if self._model is None:
            return []

        # Write to in-memory WAV for faster-whisper
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)

        segments_iter, _info = self._model.transcribe(
            buf,
            language=self.language,
            beam_size=3,              # smaller beam for speed
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300),
            condition_on_previous_text=False,
            without_timestamps=False,
        )

        result = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                result.append(LiveSegment(
                    start=round(seg.start + offset, 2),
                    end=round(seg.end + offset, 2),
                    text=text,
                ))
        return result

    def _is_duplicate(self, seg: LiveSegment) -> bool:
        """Check if a segment is a duplicate of an already-finalized segment."""
        if not self._finalized:
            return False

        # Check last few finalized segments for text overlap
        for existing in self._finalized[-5:]:
            # Exact match
            if existing.text == seg.text:
                return True
            # Time overlap with similar text
            if (abs(existing.start - seg.start) < 2.0 and
                abs(existing.end - seg.end) < 2.0):
                # Check if text is substantially similar
                if _text_similarity(existing.text, seg.text) > 0.7:
                    return True
        return False


def _text_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.streaming import StreamingTranscriber, LiveUpdate, LiveSegment; print('OK')"
```

---

### Task 2: Modify `listener/recorder.py` — Add Audio Hook for Streaming

- **File:** `/Users/alperencngzz/Desktop/listener/listener/recorder.py`
- **Action:** MODIFY
- **Details:** Add an optional callback hook so the Recorder can feed audio to the StreamingTranscriber without tight coupling.

**Change 1:** Add `on_audio` parameter to `__init__`:

Find:
```python
    def __init__(self, device: int | None = None, channels: int = 1):
        self.device = device
        self.channels = channels
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._recording = False
        self._start_time: float = 0
        self._output_path: str = ""
```

Replace with:
```python
    def __init__(self, device: int | None = None, channels: int = 1, on_audio=None):
        self.device = device
        self.channels = channels
        self.on_audio = on_audio  # optional callback: fn(numpy_array, sample_rate)
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._recording = False
        self._start_time: float = 0
        self._output_path: str = ""
        self._sample_rate: int = 0
```

**Change 2:** Store sample_rate and call hook in start():

Find in `start()` method:
```python
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=sample_rate,
            channels=self.channels,
            callback=self._audio_callback,
            blocksize=4096,
        )
        self._stream.start()
```

Replace with:
```python
        self._sample_rate = sample_rate
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=sample_rate,
            channels=self.channels,
            callback=self._audio_callback,
            blocksize=4096,
        )
        self._stream.start()
```

**Change 3:** Update `_audio_callback` to call the hook:

Find:
```python
    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"  Audio warning: {status}", file=sys.stderr)
        if self._recording and self._file is not None:
            self._file.write(indata.copy())
```

Replace with:
```python
    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"  Audio warning: {status}", file=sys.stderr)
        if self._recording and self._file is not None:
            self._file.write(indata.copy())
        # Feed audio to streaming transcriber if hook is set
        if self._recording and self.on_audio is not None:
            try:
                self.on_audio(indata.copy(), self._sample_rate)
            except Exception:
                pass  # never let the hook crash the audio callback
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.recorder import Recorder; r = Recorder(on_audio=lambda d,sr: None); print('OK')"
```

---

### Task 3: Modify `listener/web/app.py` — Add SSE Endpoint and Live Transcription Support

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:** Add SSE endpoint, modify start/stop to support streaming mode.

**IMPORTANT:** Read this file fresh before editing — it has been modified by F1, F8, and F10.

**Change 1:** Add import for `Response` and `stream_with_context`:

Find:
```python
from flask import Flask, render_template, jsonify, request, send_from_directory
```

Replace with:
```python
from flask import Flask, render_template, jsonify, request, send_from_directory, Response, stream_with_context
```

**Change 2:** Add `_streamer` to global state:

Find:
```python
_recorder = None
```

Replace with:
```python
_recorder = None
_streamer = None  # StreamingTranscriber instance for live transcription
```

**Change 3:** Modify `api_start()` to support live transcription:

Find in `api_start()`:
```python
    data = request.json or {}
    device_val = data.get("device")
    device = int(device_val) if device_val is not None and device_val != "" else None
    language = data.get("language") or None
    model_size = data.get("model_size", "large-v3")
    skip_analysis = data.get("skip_analysis", False)

    from listener.recorder import Recorder

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audio_path = str(OUTPUT_DIR / f"{session_id}.wav")

    recorder = Recorder(device=device)
    try:
        recorder.start(audio_path)
    except Exception as e:
        return jsonify({"error": f"Could not start recording: {e}"}), 500
```

Replace with:
```python
    global _streamer
    data = request.json or {}
    device_val = data.get("device")
    device = int(device_val) if device_val is not None and device_val != "" else None
    language = data.get("language") or None
    model_size = data.get("model_size", "large-v3")
    skip_analysis = data.get("skip_analysis", False)
    live_transcription = data.get("live_transcription", False)

    from listener.recorder import Recorder

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audio_path = str(OUTPUT_DIR / f"{session_id}.wav")

    # Set up streaming transcriber if live mode is requested
    audio_hook = None
    if live_transcription:
        try:
            from listener.streaming import StreamingTranscriber
            streamer = StreamingTranscriber(
                model_size=model_size,
                language=language,
            )
            streamer.start()
            _streamer = streamer
            audio_hook = streamer.feed_audio
            logger.info("Live transcription enabled for session %s", session_id)
        except Exception as e:
            logger.warning("Could not start live transcription: %s", e)
            _streamer = None

    recorder = Recorder(device=device, on_audio=audio_hook)
    try:
        recorder.start(audio_path)
    except Exception as e:
        if _streamer:
            _streamer.stop()
            _streamer = None
        return jsonify({"error": f"Could not start recording: {e}"}), 500
```

**Change 4:** Modify `api_stop()` to stop the streamer:

Find in `api_stop()`:
```python
    recorder.stop()

    threading.Thread(
```

Replace with:
```python
    recorder.stop()

    # Stop live transcription if active
    if _streamer is not None:
        try:
            _streamer.stop()
        except Exception as e:
            logger.warning("Error stopping live transcription: %s", e)
        _streamer = None

    threading.Thread(
```

**Change 5:** Add SSE endpoint for live streaming. Add this new route BEFORE the `# Background processing` section (after the webhooks test endpoint):

```python
# ---------------------------------------------------------------------------
# Live Transcription SSE (F9)
# ---------------------------------------------------------------------------

@app.route("/api/live-stream")
def api_live_stream():
    """Server-Sent Events endpoint for real-time transcript updates.

    Returns a stream of SSE events with partial transcription results.
    Each event is a JSON object with:
      - finalized: list of {start, end, text} segments (confirmed)
      - tentative: string (current unconfirmed text, may change)
      - elapsed: float (seconds since recording started)
      - done: bool (true when recording/transcription is complete)
    """
    def generate():
        streamer = _streamer
        if streamer is None:
            # No live transcription active — send error and close
            data = json.dumps({"error": "No live transcription active", "done": True})
            yield f"data: {data}\n\n"
            return

        for update in streamer.updates():
            data = {
                "finalized": [
                    {"start": s.start, "end": s.end, "text": s.text}
                    for s in update.finalized_segments
                ],
                "tentative": update.tentative_text,
                "elapsed": round(update.elapsed, 1),
                "done": update.done,
            }
            if update.error:
                data["error"] = update.error
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            if update.done:
                return

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('Routes:'); [print(f'  {r.rule} [{r.methods}]') for r in app.url_map.iter_rules() if 'live' in r.rule or 'static' not in r.rule]"
```

---

### Task 4: Modify `listener/web/templates/index.html` — Live Transcript UI

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:** Add live transcript display, checkbox toggle, and SSE client logic.

**IMPORTANT:** Read this file fresh. It's been modified by F1, F8, and F10.

#### 4a: Add CSS for live transcript

Find:
```css
.wh-empty{text-align:center;color:#94a3b8;font-size:13px;padding:12px 0}
</style>
```

Replace with:
```css
.wh-empty{text-align:center;color:#94a3b8;font-size:13px;padding:12px 0}

/* ---- Live Transcription (F9) ---- */
.live-panel{
  display:none;margin-top:12px;
  background:#f8fafc;border-radius:10px;border:1px solid #e2e8f0;
  overflow:hidden;
}
.live-panel.open{display:block;animation:fadeUp .3s ease}
.live-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 16px;background:#eef2ff;border-bottom:1px solid #e2e8f0;
}
.live-header h3{font-size:13px;font-weight:700;color:#6366f1;margin:0}
.live-dot{
  width:8px;height:8px;border-radius:50%;background:#ef4444;
  animation:livePulse 1.5s ease-in-out infinite;
}
@keyframes livePulse{0%,100%{opacity:1}50%{opacity:.3}}
.live-body{
  max-height:280px;overflow-y:auto;padding:14px 16px;
  font-size:13px;line-height:1.7;color:#334155;
}
.live-body::-webkit-scrollbar{width:5px}
.live-body::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
.live-finalized{color:#1e293b}
.live-finalized .live-seg{margin-bottom:4px}
.live-finalized .live-ts{
  font-family:'SF Mono',SFMono-Regular,Menlo,monospace;
  font-size:11px;color:#6366f1;background:#eef2ff;
  padding:1px 4px;border-radius:3px;margin-right:4px;
}
.live-tentative{
  color:#94a3b8;font-style:italic;
  border-left:2px solid #c7d2fe;padding-left:10px;margin-top:6px;
}
.live-empty{color:#94a3b8;font-size:12px;text-align:center;padding:20px 0}
.live-status{
  padding:6px 16px;font-size:11px;color:#94a3b8;
  background:#f1f5f9;border-top:1px solid #e2e8f0;
  display:flex;align-items:center;gap:6px;
}
</style>
```

#### 4b: Add live transcription checkbox to settings card

Find:
```html
      <div class="cb-row">
        <input type="checkbox" id="skip-analysis">
        <label for="skip-analysis">Skip Claude analysis (transcript only)</label>
      </div>
```

Replace with:
```html
      <div class="cb-row">
        <input type="checkbox" id="skip-analysis">
        <label for="skip-analysis">Skip Claude analysis (transcript only)</label>
      </div>
      <div class="cb-row">
        <input type="checkbox" id="live-transcription" checked>
        <label for="live-transcription">Live transcription (show text while recording)</label>
      </div>
```

#### 4c: Add live transcript panel after the recorder card

Find:
```html
  <!-- Viewer -->
  <div class="card viewer" id="viewer">
```

Replace with:
```html
  <!-- Live Transcription Panel (F9) -->
  <div class="live-panel" id="live-panel">
    <div class="live-header">
      <h3>Live Transcript</h3>
      <div class="live-dot" id="live-dot"></div>
    </div>
    <div class="live-body" id="live-body">
      <div class="live-empty">Waiting for speech...</div>
    </div>
    <div class="live-status" id="live-status">Connecting...</div>
  </div>

  <!-- Viewer -->
  <div class="card viewer" id="viewer">
```

#### 4d: Add `live_transcription` to the start request in `doStart()`

Find:
```javascript
async function doStart(){
  const body={
    device:val('device')!==''?parseInt(val('device')):null,
    language:val('language')||null,
    skip_analysis:Q('#skip-analysis').checked,
  };
```

Replace with:
```javascript
async function doStart(){
  const liveEnabled=Q('#live-transcription').checked;
  const body={
    device:val('device')!==''?parseInt(val('device')):null,
    language:val('language')||null,
    skip_analysis:Q('#skip-analysis').checked,
    live_transcription:liveEnabled,
  };
```

#### 4e: Start/stop live stream in doStart/doStop

Find (in `doStart`, after `closeViewer();`):
```javascript
  setUI('recording');
  closeViewer();
  recStart=Date.now();
  tRef=setInterval(tick,100);
```

Replace with:
```javascript
  setUI('recording');
  closeViewer();
  recStart=Date.now();
  tRef=setInterval(tick,100);
  if(Q('#live-transcription').checked) startLiveStream();
```

Find (in `doStop`):
```javascript
async function doStop(){
  await f('/api/stop',{method:'POST'});
  clearInterval(tRef);tRef=null;
  setUI('processing');
}
```

Replace with:
```javascript
async function doStop(){
  stopLiveStream();
  await f('/api/stop',{method:'POST'});
  clearInterval(tRef);tRef=null;
  setUI('processing');
}
```

#### 4f: Disable live-transcription checkbox during recording (in `setUI`)

Find:
```javascript
  const lock=state==='recording'||state==='processing';
  Q('#device').disabled=lock;
  Q('#language').disabled=lock;
  Q('#skip-analysis').disabled=lock;
```

Replace with:
```javascript
  const lock=state==='recording'||state==='processing';
  Q('#device').disabled=lock;
  Q('#language').disabled=lock;
  Q('#skip-analysis').disabled=lock;
  Q('#live-transcription').disabled=lock;
```

#### 4g: Add live streaming JavaScript functions

Add the following block **before** the `// Sessions list` section (i.e., before `async function loadSessions(){`):

```javascript
// ===========================================================================
// Live Transcription (F9)
// ===========================================================================
let _evtSource = null;

function startLiveStream(){
  const panel = Q('#live-panel');
  const body = Q('#live-body');
  const status = Q('#live-status');

  panel.classList.add('open');
  body.innerHTML = '<div class="live-empty">Waiting for speech...</div>';
  status.textContent = 'Connecting...';

  // Small delay to let the server start the streamer
  setTimeout(() => {
    _evtSource = new EventSource('/api/live-stream');

    _evtSource.onmessage = function(e) {
      try {
        const data = JSON.parse(e.data);

        if (data.error && data.done) {
          status.textContent = 'Live transcription not available';
          return;
        }

        if (data.done) {
          status.textContent = 'Transcription complete';
          Q('#live-dot').style.display = 'none';
          closeLiveStream();
          return;
        }

        renderLiveTranscript(data);
        status.textContent = `Live \u2022 ${fmtT(data.elapsed || 0)}`;
      } catch (err) {
        console.warn('SSE parse error:', err);
      }
    };

    _evtSource.onerror = function() {
      status.textContent = 'Connection lost \u2014 reconnecting...';
    };

    _evtSource.onopen = function() {
      status.textContent = 'Connected \u2014 listening...';
    };
  }, 1500);
}

function stopLiveStream(){
  closeLiveStream();
  // Keep panel visible with final content until viewer opens
}

function closeLiveStream(){
  if (_evtSource) {
    _evtSource.close();
    _evtSource = null;
  }
}

function renderLiveTranscript(data){
  const body = Q('#live-body');
  let html = '';

  // Finalized segments
  const segs = data.finalized || [];
  if (segs.length > 0) {
    html += '<div class="live-finalized">';
    segs.forEach(seg => {
      const ts = fmtLiveTs(seg.start);
      html += `<div class="live-seg"><span class="live-ts">${ts}</span>${esc(seg.text)}</div>`;
    });
    html += '</div>';
  }

  // Tentative text
  if (data.tentative) {
    html += `<div class="live-tentative">${esc(data.tentative)}</div>`;
  }

  if (!html) {
    html = '<div class="live-empty">Waiting for speech...</div>';
  }

  body.innerHTML = html;
  // Auto-scroll to bottom
  body.scrollTop = body.scrollHeight;
}

function fmtLiveTs(secs){
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return pad(m) + ':' + pad(s);
}
```

#### 4h: Hide live panel when viewer opens

Find:
```javascript
function closeViewer(){
  Q('#viewer').classList.remove('open');
  viewerSession=null;
```

Replace with:
```javascript
function closeViewer(){
  Q('#viewer').classList.remove('open');
  Q('#live-panel').classList.remove('open');
  closeLiveStream();
  viewerSession=null;
```

Also, when transitioning to "done" state and opening the viewer, hide the live panel. Find in the `poll()` function:

```javascript
      if(d.status==='done'){
        setUI('done');
        // Build session object from state and open viewer
        const sess={id:d.session_id,title:d.title||'',files:d.files,duration:0,language:''};
        openViewer(sess);
        loadSessions();
```

Replace with:
```javascript
      if(d.status==='done'){
        setUI('done');
        Q('#live-panel').classList.remove('open');
        closeLiveStream();
        // Build session object from state and open viewer
        const sess={id:d.session_id,title:d.title||'',files:d.files,duration:0,language:''};
        openViewer(sess);
        loadSessions();
```

- **Verification:** Start the web server, load the page, verify the "Live transcription" checkbox appears in settings and the live panel is hidden by default.

---

### Task 5: Update `listener/cli.py` — Add `--live` Flag (Optional Enhancement)

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** MODIFY
- **Details:** Add a `--live` flag to the `record` command that prints live transcript to the terminal.

**IMPORTANT:** Read this file fresh before editing.

Find the `record` command's Click options. Look for the line that defines the `record` function and its decorators. Add after the last `@click.option`:

```python
@click.option("--live/--no-live", default=False, help="Show live transcription while recording")
```

Then in the record function body, after the recorder starts and before the "Press Enter to stop" prompt, if `live` is True:

```python
    if live:
        from listener.streaming import StreamingTranscriber
        import sys

        streamer = StreamingTranscriber(model_size=model_size, language=language)
        streamer.start()

        # Hook up audio feed
        original_callback = recorder._audio_callback
        def live_callback(indata, frames, time_info, status):
            original_callback(indata, frames, time_info, status)
            try:
                streamer.feed_audio(indata.copy(), recorder._sample_rate)
            except Exception:
                pass
        recorder._stream.callback = live_callback

        click.echo("Live transcription enabled. Text will appear as you speak.\n")

        # Print updates in background thread
        import threading
        def print_updates():
            last_finalized_count = 0
            for update in streamer.updates():
                new_segs = update.finalized_segments[last_finalized_count:]
                for seg in new_segs:
                    ts = f"{int(seg.start//60):02d}:{int(seg.start%60):02d}"
                    click.echo(f"  [{ts}] {seg.text}")
                last_finalized_count = len(update.finalized_segments)
                if update.tentative_text:
                    sys.stdout.write(f"\r  ... {update.tentative_text[:80]}")
                    sys.stdout.flush()
                if update.done:
                    sys.stdout.write("\r" + " " * 90 + "\r")
                    break

        update_thread = threading.Thread(target=print_updates, daemon=True)
        update_thread.start()
```

**Note:** This CLI enhancement is optional. The primary deliverable is the web UI. If the CLI integration is too complex to wire up cleanly with the existing `record` command flow, skip it and add a comment noting it as future work.

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -m listener record --help | grep -i live
```

---

## Frontend Changes Summary

All changes are in `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`:

| What | Where | Description |
|---|---|---|
| CSS: `.live-panel`, `.live-header`, `.live-body`, `.live-finalized`, `.live-tentative`, `.live-status`, `.live-dot` | After `.wh-empty` rule, before `</style>` | Styling for the live transcript panel |
| HTML: Live transcription checkbox | Inside `#settings-card`, after skip-analysis checkbox | Toggle for enabling/disabling live mode |
| HTML: Live transcript panel | Between recorder card and viewer div | Panel showing real-time text with finalized/tentative regions |
| JS: `startLiveStream()`, `stopLiveStream()`, `closeLiveStream()`, `renderLiveTranscript()`, `fmtLiveTs()` | New section before Sessions list section | SSE EventSource connection and live rendering |
| JS: Modified `doStart()` | Existing function | Sends `live_transcription` flag and calls `startLiveStream()` |
| JS: Modified `doStop()` | Existing function | Calls `stopLiveStream()` before stopping |
| JS: Modified `setUI()` | Existing function | Disables live checkbox during recording/processing |
| JS: Modified `closeViewer()` | Existing function | Also hides live panel |
| JS: Modified `poll()` | Existing function | Hides live panel on "done" state |

---

## Flask Endpoint Changes Summary

| Endpoint | Method | Action | Description |
|---|---|---|---|
| `/api/start` | POST | MODIFY | Accept `live_transcription` boolean; create `StreamingTranscriber` if true |
| `/api/stop` | POST | MODIFY | Stop `StreamingTranscriber` if active |
| `/api/live-stream` | GET | NEW | SSE endpoint streaming `LiveUpdate` objects as JSON events |

---

## Final Verification

### 1. Import Check
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "from listener.streaming import StreamingTranscriber, LiveUpdate, LiveSegment; print('streaming.py OK')"
python -c "from listener.recorder import Recorder; r=Recorder(on_audio=lambda d,sr:None); print('recorder.py OK')"
python -c "from listener.web.app import app; print('app.py OK')"
```

### 2. Route Check
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "
from listener.web.app import app
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    if 'static' not in rule.rule:
        print(f'{rule.rule} -> {rule.methods}')
" | grep -E "(live|start|stop)"
```

Expected output should include:
```
/api/live-stream -> {'GET', ...}
/api/start -> {'POST', ...}
/api/stop -> {'POST', ...}
```

### 3. Web UI Test
```bash
cd /Users/alperencngzz/Desktop/listener && python -m listener web
```

Then in browser at `http://127.0.0.1:8642`:
1. Verify "Live transcription" checkbox appears in Settings
2. Check the checkbox and click Record
3. The live transcript panel should appear below the recorder
4. Speak — text should appear within 5-8 seconds
5. Finalized text should be in regular style, tentative in gray italic
6. Click Stop — live panel should close and standard processing begins
7. When processing completes, the viewer opens with the full transcript

### 4. Non-Live Mode Test
1. Uncheck "Live transcription" checkbox
2. Click Record, speak, click Stop
3. No live panel should appear
4. Processing should work exactly as before (no regression)

---

## Known Limitations & Future Improvements

1. **Latency:** Whisper large-v3 takes 2-5 seconds per 5-second chunk on CPU. For lower latency, users could switch to `base` or `small` model via settings.
2. **Memory:** The Whisper model stays loaded in memory (~3-4 GB for large-v3) during the entire recording session. This is in addition to any model loaded for post-recording transcription.
3. **Duplicate Detection:** The simple word-overlap similarity check may miss some edge cases. A more sophisticated approach could use edit distance or sequence alignment.
4. **No Speaker Labels in Live Mode:** Live transcription doesn't run diarization (too slow for real-time). Speaker labels are only added during post-recording processing.
5. **Resampling:** The simple decimation resampling in `feed_audio()` is approximate. For production quality, consider using `scipy.signal.resample` or `librosa.resample`.
6. **CLI `--live` flag:** The CLI integration is a nice-to-have but the primary deliverable is the web UI. The CLI version is simpler (text-only, no SSE).
