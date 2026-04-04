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
