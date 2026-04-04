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
