"""Structured JSON export for meeting transcripts.

Outputs a well-defined JSON schema for maximum interoperability
with other tools. Uses ensure_ascii=False to preserve Turkish characters.
"""

import io
import json
import re
from datetime import datetime, timezone


def export_json(
    session_id: str,
    title: str = "",
    date_str: str = "",
    duration_str: str = "",
    language: str = "",
    transcript_text: str = "",
    analysis_text: str | None = None,
    segments: list[dict] | None = None,
    duration_seconds: float = 0.0,
    language_confidence: float = 0.0,
    recipe_id: str = "",
) -> io.BytesIO:
    """Generate a structured JSON export.

    Args:
        session_id: Session identifier.
        title: Meeting title.
        date_str: Human-readable date string.
        duration_str: Duration string (e.g., "45m 12s").
        language: Language code.
        transcript_text: Timestamped transcript markdown text.
        analysis_text: Optional analysis text.
        segments: Optional pre-parsed segment list.
        duration_seconds: Total audio duration in seconds.
        language_confidence: Language detection confidence (0-1).
        recipe_id: Recipe ID used for analysis.

    Returns:
        BytesIO containing the JSON file bytes.
    """
    # Parse date from session_id
    try:
        dt = datetime.strptime(session_id, "%Y-%m-%d_%H-%M-%S")
        date_iso = dt.strftime("%Y-%m-%d")
    except ValueError:
        date_iso = session_id.split("_")[0] if "_" in session_id else session_id

    # Build segments list
    if segments:
        json_segments = [
            {
                "index": i,
                "start": seg["start"],
                "end": seg["end"],
                "text": seg.get("text", ""),
                "speaker": seg.get("speaker", ""),
            }
            for i, seg in enumerate(segments)
        ]
    else:
        json_segments = _parse_segments_from_text(transcript_text)

    doc = {
        "schema_version": "1.0",
        "generator": "listener",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {
            "session_id": session_id,
            "title": title,
            "date": date_iso,
            "duration_seconds": duration_seconds,
            "language": language,
            "language_confidence": language_confidence,
        },
        "transcript": {
            "full_text": transcript_text,
            "segments": json_segments,
            "segment_count": len(json_segments),
        },
        "analysis": {
            "content": analysis_text or "",
            "recipe": recipe_id or "",
        },
    }

    buf = io.BytesIO()
    content = json.dumps(doc, ensure_ascii=False, indent=2)
    buf.write(content.encode("utf-8"))
    buf.seek(0)
    return buf


def _parse_segments_from_text(transcript_text: str) -> list[dict]:
    """Parse segments from markdown transcript text."""
    pattern = re.compile(
        r'(?:\*\*)?\[(\d{1,2}:\d{2}(?::\d{2})?)\](?:\s*([A-Z_0-9]+):?\*\*)?(.+?)$',
        re.MULTILINE,
    )
    segments = []
    matches = list(pattern.finditer(transcript_text))
    for i, m in enumerate(matches):
        ts_str = m.group(1)
        speaker = m.group(2) or ""
        text = m.group(3).strip()
        start = _parse_ts(ts_str)

        if i + 1 < len(matches):
            end = _parse_ts(matches[i + 1].group(1))
        else:
            end = start + 5.0

        if end <= start:
            end = start + 3.0

        segments.append({
            "index": i,
            "start": start,
            "end": end,
            "text": text,
            "speaker": speaker,
        })

    return segments


def _parse_ts(ts: str) -> float:
    """Parse MM:SS or HH:MM:SS to seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0.0
