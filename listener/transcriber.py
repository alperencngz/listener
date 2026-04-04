"""Speech-to-text transcription using faster-whisper.

Uses CTranslate2 backend for efficient inference on Apple Silicon.
Supports English, Turkish, and auto language detection.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    """A transcribed speech segment."""
    start: float
    end: float
    text: str
    speaker: str = ""


@dataclass
class TranscriptionResult:
    """Complete transcription output."""
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def has_speakers(self) -> bool:
        return any(s.speaker for s in self.segments)

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


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcribe(
    audio_path: str,
    model_size: str = "large-v3",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "auto",
) -> TranscriptionResult:
    """Transcribe an audio file using faster-whisper.

    Args:
        audio_path: Path to audio file.
        model_size: Whisper model size (tiny, base, small, medium, large-v3).
        language: Language code ('en', 'tr') or None for auto-detect.
        device: Compute device ('cpu').
        compute_type: Quantization ('auto', 'int8', 'float32').

    Returns:
        TranscriptionResult with segments and metadata.
    """
    from faster_whisper import WhisperModel

    print(f"Loading Whisper {model_size} model (first run downloads ~3GB)...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(f"Transcribing {Path(audio_path).name}...")
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
    )

    print(f"Detected language: {info.language} ({info.language_probability:.0%} confidence)")

    segments = []
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            segments.append(Segment(start=seg.start, end=seg.end, text=text))
            ts = _fmt_ts(seg.start)
            preview = text[:80] + ("..." if len(text) > 80 else "")
            print(f"  [{ts}] {preview}")

    print(f"Done: {len(segments)} segments, {info.duration:.0f}s audio")

    return TranscriptionResult(
        segments=segments,
        language=info.language,
        language_probability=info.language_probability,
        duration=info.duration,
    )
