"""SRT (SubRip Subtitle) export for meeting transcripts.

Pure Python — no external dependencies. Generates standard SRT format
from transcript text with timestamps.
"""

import io
import re


def export_srt(
    session_id: str,
    title: str = "",
    date_str: str = "",
    duration_str: str = "",
    language: str = "",
    transcript_text: str = "",
    analysis_text: str | None = None,
    segments: list[dict] | None = None,
) -> io.BytesIO:
    """Generate an SRT subtitle file from transcript segments.

    Prefers `segments` (list of {start, end, text, speaker?} dicts) if provided.
    Otherwise parses timestamps from transcript_text markdown.

    Args:
        session_id: Session identifier.
        title: Not used for SRT (ignored).
        date_str: Not used for SRT (ignored).
        duration_str: Not used for SRT (ignored).
        language: Not used for SRT (ignored).
        transcript_text: Timestamped transcript markdown text.
        analysis_text: Not used for SRT (ignored).
        segments: Optional pre-parsed list of segment dicts with start/end/text/speaker.

    Returns:
        BytesIO containing UTF-8 SRT file content.
    """
    if segments:
        entries = _segments_to_srt(segments)
    else:
        entries = _parse_transcript_to_srt(transcript_text)

    buf = io.BytesIO()
    buf.write(entries.encode("utf-8"))
    buf.seek(0)
    return buf


def _segments_to_srt(segments: list[dict]) -> str:
    """Convert segment dicts to SRT format."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _seconds_to_srt_time(seg["start"])
        end = _seconds_to_srt_time(seg["end"])
        text = seg.get("text", "").strip()
        speaker = seg.get("speaker", "")
        if speaker:
            text = f"[{speaker}] {text}"

        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")  # blank separator

    return "\n".join(lines)


def _parse_transcript_to_srt(transcript_text: str) -> str:
    """Parse timestamped markdown text into SRT format.

    Handles both formats:
      [MM:SS] Text here
      **[MM:SS] SPEAKER_00:** Text here
    """
    entries = []
    pattern = re.compile(
        r'(?:\*\*)?\[(\d{1,2}:\d{2}(?::\d{2})?)\](?:\s*([A-Z_0-9]+):?\*\*)?(.+?)$',
        re.MULTILINE,
    )

    matches = list(pattern.finditer(transcript_text))
    for i, m in enumerate(matches):
        ts_str = m.group(1)
        speaker = m.group(2) or ""
        text = m.group(3).strip()

        start_seconds = _parse_timestamp(ts_str)

        # End time: use next segment's start, or start + 5 seconds
        if i + 1 < len(matches):
            next_ts = matches[i + 1].group(1)
            end_seconds = _parse_timestamp(next_ts)
        else:
            end_seconds = start_seconds + 5.0

        # Ensure end > start
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + 3.0

        start_srt = _seconds_to_srt_time(start_seconds)
        end_srt = _seconds_to_srt_time(end_seconds)

        display_text = f"[{speaker}] {text}" if speaker else text

        entries.append(f"{i + 1}\n{start_srt} --> {end_srt}\n{display_text}\n")

    return "\n".join(entries)


def _parse_timestamp(ts: str) -> float:
    """Parse MM:SS or HH:MM:SS to seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0.0


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
