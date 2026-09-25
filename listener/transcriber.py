"""Speech-to-text transcription using faster-whisper.

Uses CTranslate2 backend for efficient inference on Apple Silicon.
Supports English, Turkish, and auto language detection.

Supports resumable transcription: partial results are saved after each
segment so the process can continue from where it left off if interrupted.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class TranscriptionInterrupted(Exception):
    """Raised when ``should_stop`` asked us to stop; the checkpoint is kept on disk."""


@dataclass
class Word:
    """A transcribed word with precise timing."""
    start: float
    end: float
    text: str
    probability: float = 0.0


@dataclass
class Segment:
    """A transcribed speech segment."""
    start: float
    end: float
    text: str
    speaker: str = ""
    words: list[Word] = field(default_factory=list)


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


def _checkpoint_path(audio_path: str) -> Path:
    """Return the path for the transcription checkpoint file."""
    p = Path(audio_path)
    return p.parent / f"{p.stem}_checkpoint.json"


def _save_checkpoint(audio_path: str, segments: list[Segment], language: str,
                     language_probability: float, duration: float):
    """Save partial transcription progress to disk."""
    data = {
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [
                    {"start": w.start, "end": w.end, "text": w.text,
                     "probability": w.probability}
                    for w in s.words
                ],
            }
            for s in segments
        ],
        "language": language,
        "language_probability": language_probability,
        "duration": duration,
        "last_end": segments[-1].end if segments else 0.0,
    }
    _checkpoint_path(audio_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_checkpoint(audio_path: str) -> dict | None:
    """Load partial transcription checkpoint if it exists."""
    cp = _checkpoint_path(audio_path)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        if data.get("segments"):
            return data
    except Exception:
        pass
    return None


def _clear_checkpoint(audio_path: str):
    """Remove checkpoint file after successful completion."""
    _checkpoint_path(audio_path).unlink(missing_ok=True)


def transcribe(
    audio_path: str,
    model_size: str = "large-v3",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "auto",
    multilingual: bool = False,
    hotwords: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[int, float, float], None] | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file using faster-whisper.

    Supports resuming from a checkpoint if a previous transcription was
    interrupted. Partial results are saved after each segment.

    Args:
        audio_path: Path to audio file.
        model_size: Whisper model size (tiny, base, small, medium, large-v3).
        language: Language code ('en', 'tr') or None for auto-detect.
        device: Compute device ('cpu').
        compute_type: Quantization ('auto', 'int8', 'float32').
        multilingual: Re-detect language per 30s window — needed for
            code-switching speech (e.g. Turkish with English sentences).
        hotwords: Domain terms to bias decoding toward (names, jargon).
        should_stop: Optional callback polled after every segment. When it
            returns True the checkpoint is left on disk and
            TranscriptionInterrupted is raised (resume by calling again).
        on_progress: Optional callback ``(segments_done, last_end_seconds,
            audio_duration_seconds)`` called after every segment.

    Returns:
        TranscriptionResult with segments and metadata.

    Raises:
        TranscriptionInterrupted: if ``should_stop`` returned True.
    """
    from faster_whisper import WhisperModel

    # Check for existing checkpoint
    checkpoint = _load_checkpoint(audio_path)
    resumed_segments: list[Segment] = []
    resume_from = 0.0

    if checkpoint:
        resumed_segments = [
            Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                words=[
                    Word(start=w["start"], end=w["end"], text=w["text"],
                         probability=w.get("probability", 0.0))
                    for w in s.get("words", [])
                ],
            )
            for s in checkpoint["segments"]
        ]
        resume_from = checkpoint["last_end"]
        # Use language from checkpoint if not explicitly provided
        # (skip when multilingual — language is re-detected per window)
        if not language and not multilingual and checkpoint.get("language"):
            language = checkpoint["language"]
        print(f"Resuming transcription from {_fmt_ts(resume_from)} "
              f"({len(resumed_segments)} segments already done)")

    print(f"Loading Whisper {model_size} model (first run downloads ~3GB)...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(f"Transcribing {Path(audio_path).name}...")

    # If resuming, use clip_timestamps to skip already-transcribed audio
    transcribe_kwargs = dict(
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
        word_timestamps=True,
        multilingual=multilingual,
        hotwords=hotwords,
    )
    if resume_from > 0:
        transcribe_kwargs["clip_timestamps"] = [resume_from]

    segments_iter, info = model.transcribe(audio_path, **transcribe_kwargs)

    if not resumed_segments:
        print(f"Detected language: {info.language} "
              f"({info.language_probability:.0%} confidence)")

    detected_lang = language or info.language
    detected_prob = info.language_probability

    segments = list(resumed_segments)
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            words = [
                Word(
                    start=float(w.start),
                    end=float(w.end),
                    text=w.word.strip(),
                    probability=float(getattr(w, "probability", 0.0) or 0.0),
                )
                for w in (seg.words or [])
                if w.word and w.word.strip()
            ]
            segments.append(Segment(
                start=seg.start, end=seg.end, text=text, words=words,
            ))
            ts = _fmt_ts(seg.start)
            preview = text[:80] + ("..." if len(text) > 80 else "")
            print(f"  [{ts}] {preview}")

            # Save checkpoint after every segment
            _save_checkpoint(
                audio_path, segments, detected_lang,
                detected_prob, info.duration,
            )
            if on_progress is not None:
                try:
                    on_progress(len(segments), float(seg.end), float(info.duration))
                except Exception:
                    pass
            if should_stop is not None and should_stop():
                print(f"Transcription interrupted at {_fmt_ts(seg.end)}; checkpoint kept.")
                raise TranscriptionInterrupted(
                    f"Stopped at {_fmt_ts(seg.end)} ({len(segments)} segments saved to checkpoint)"
                )

    # Transcription complete — remove checkpoint
    _clear_checkpoint(audio_path)

    print(f"Done: {len(segments)} segments, {info.duration:.0f}s audio")

    return TranscriptionResult(
        segments=segments,
        language=detected_lang,
        language_probability=detected_prob,
        duration=info.duration,
    )
