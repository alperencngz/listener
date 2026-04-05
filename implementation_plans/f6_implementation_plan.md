# Implementation Plan: F6 — Multi-Format Export

## Pre-Implementation Checklist

Before writing any code, the implementor MUST read these files fresh (they have been modified by previous features F1–F5, F7–F10):

- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (currently ~684 lines)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Single-file frontend
- `/Users/alperencngzz/Desktop/listener/listener/cli.py` — CLI commands (currently ~433 lines)
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Dependencies
- `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` — `Segment` and `TranscriptionResult` dataclasses

Verify:
- `python-docx` is NOT yet in pyproject.toml
- `fpdf2` is NOT yet in pyproject.toml
- The directory `listener/export/` does NOT yet exist

## Dependencies

### Add to pyproject.toml

Add these two packages to the `dependencies` list in `/Users/alperencngzz/Desktop/listener/pyproject.toml`:

```
"python-docx>=1.1.0",
"fpdf2>=2.8.0",
```

### Install

```bash
cd /Users/alperencngzz/Desktop/listener && pip install -e .
```

### External: DejaVu Sans Font

Download DejaVuSans.ttf and DejaVuSans-Bold.ttf for PDF Turkish character support. These must be bundled at:
- `/Users/alperencngzz/Desktop/listener/listener/export/fonts/DejaVuSans.ttf`
- `/Users/alperencngzz/Desktop/listener/listener/export/fonts/DejaVuSans-Bold.ttf`

Download command:
```bash
mkdir -p /Users/alperencngzz/Desktop/listener/listener/export/fonts
cd /Users/alperencngzz/Desktop/listener/listener/export/fonts
curl -L -o DejaVuSans.ttf "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
curl -L -o DejaVuSans-Bold.ttf "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
```

If the download fails, use a fallback: the PDF exporter should catch `FileNotFoundError` for the font and fall back to fpdf2's built-in Helvetica (with a warning that Turkish chars may not render).

---

## Implementation Tasks

### Task 1: Create export package init

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/__init__.py`
- **Action:** Create
- **Details:**

```python
"""Multi-format export for Listener meeting transcripts.

Supported formats: DOCX, PDF, SRT, JSON.
"""

from listener.export.docx_export import export_docx
from listener.export.pdf_export import export_pdf
from listener.export.srt_export import export_srt
from listener.export.json_export import export_json

EXPORTERS = {
    "docx": export_docx,
    "pdf": export_pdf,
    "srt": export_srt,
    "json": export_json,
}

__all__ = ["export_docx", "export_pdf", "export_srt", "export_json", "EXPORTERS"]
```

- **Verification:** `python -c "from listener.export import EXPORTERS; print(list(EXPORTERS.keys()))"`

---

### Task 2: Create DOCX exporter

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/docx_export.py`
- **Action:** Create
- **Details:**

```python
"""DOCX (Word) export for meeting transcripts."""

import io
import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH


def export_docx(
    session_id: str,
    title: str,
    date_str: str,
    duration_str: str,
    language: str,
    transcript_text: str,
    analysis_text: str | None = None,
) -> io.BytesIO:
    """Generate a DOCX file and return it as a BytesIO stream.

    Args:
        session_id: Session identifier (e.g., "2026-04-04_14-30-00").
        title: Meeting title.
        date_str: Human-readable date string.
        duration_str: Duration (e.g., "45m 12s").
        language: Language code.
        transcript_text: Full timestamped transcript markdown text.
        analysis_text: Optional analysis markdown text.

    Returns:
        BytesIO containing the DOCX file bytes.
    """
    doc = Document()

    # Title
    heading = doc.add_heading(title, level=1)

    # Metadata paragraph
    meta = doc.add_paragraph()
    meta.add_run(f"Date: {date_str}").bold = True
    meta.add_run(f"  |  Duration: {duration_str}  |  Language: {language.upper()}")
    meta.space_after = Pt(12)

    doc.add_paragraph("─" * 60)

    # Transcript section
    doc.add_heading("Transcript", level=2)
    _add_transcript_to_docx(doc, transcript_text)

    # Analysis section (if available)
    if analysis_text:
        doc.add_page_break()
        doc.add_heading("Analysis", level=2)
        _add_markdown_to_docx(doc, analysis_text)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _add_transcript_to_docx(doc: Document, transcript_text: str):
    """Add transcript lines with styled timestamps."""
    lines = transcript_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)

        # Match timestamps: [HH:MM:SS] or **[HH:MM:SS] SPEAKER:** patterns
        ts_match = re.match(
            r'(?:\*\*)?\[(\d{1,2}:\d{2}(?::\d{2})?)\](?:\s*([A-Z_0-9]+):?\*\*)?(.*)$',
            line,
        )
        if ts_match:
            ts_val = ts_match.group(1)
            speaker = ts_match.group(2) or ""
            text = ts_match.group(3).strip()

            # Timestamp in blue Courier New
            ts_run = p.add_run(f"[{ts_val}] ")
            ts_run.font.name = "Courier New"
            ts_run.font.size = Pt(10)
            ts_run.font.color.rgb = RGBColor(0x63, 0x66, 0xF1)

            # Speaker label if present
            if speaker:
                spk_run = p.add_run(f"{speaker}: ")
                spk_run.font.bold = True
                spk_run.font.size = Pt(11)

            # Text
            text_run = p.add_run(text)
            text_run.font.size = Pt(11)
        else:
            # Plain text line
            run = p.add_run(line)
            run.font.size = Pt(11)


def _add_markdown_to_docx(doc: Document, markdown_text: str):
    """Convert basic markdown to Word styles."""
    lines = markdown_text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Skip empty lines
        if not line:
            i += 1
            continue

        # Headings
        if line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("---"):
            doc.add_paragraph("─" * 60)
        elif line.startswith("- ") or line.startswith("* "):
            # Bullet list
            p = doc.add_paragraph(line[2:].strip(), style="List Bullet")
        elif re.match(r'^\d+\.\s', line):
            # Numbered list
            text = re.sub(r'^\d+\.\s*', '', line)
            p = doc.add_paragraph(text, style="List Number")
        else:
            # Regular paragraph — strip bold markers
            clean = line.replace("**", "")
            doc.add_paragraph(clean)

        i += 1
```

- **Verification:** `python -c "from listener.export.docx_export import export_docx; print('DOCX exporter OK')"`

---

### Task 3: Create PDF exporter

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/pdf_export.py`
- **Action:** Create
- **Details:**

```python
"""PDF export for meeting transcripts with Unicode/Turkish support."""

import io
import re
import logging
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).parent / "fonts"


class MeetingPDF(FPDF):
    """Custom PDF with header/footer and Unicode font support."""

    def __init__(self, title: str = "Meeting"):
        super().__init__()
        self.meeting_title = title
        self._font_loaded = False
        self._load_unicode_font()

    def _load_unicode_font(self):
        """Load DejaVu Sans for Turkish character support."""
        regular = FONTS_DIR / "DejaVuSans.ttf"
        bold = FONTS_DIR / "DejaVuSans-Bold.ttf"
        if regular.exists():
            self.add_font("dejavu", "", str(regular))
            if bold.exists():
                self.add_font("dejavu", "B", str(bold))
            self._font_loaded = True
            logger.debug("Loaded DejaVuSans font for Unicode PDF support")
        else:
            logger.warning(
                "DejaVuSans.ttf not found at %s — Turkish characters may not render. "
                "Download from https://dejavu-fonts.github.io/", FONTS_DIR
            )

    def _use_font(self, style: str = "", size: int = 11):
        """Set font, preferring DejaVu if available."""
        if self._font_loaded:
            self.set_font("dejavu", style, size)
        else:
            self.set_font("Helvetica", style, size)

    def header(self):
        self._use_font("B", 9)
        self.set_text_color(148, 163, 184)  # gray
        self.cell(0, 8, self.meeting_title, 0, 1, "L")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self._use_font("", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", 0, 0, "C")


def export_pdf(
    session_id: str,
    title: str,
    date_str: str,
    duration_str: str,
    language: str,
    transcript_text: str,
    analysis_text: str | None = None,
) -> io.BytesIO:
    """Generate a PDF file and return it as a BytesIO stream."""
    pdf = MeetingPDF(title=title)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf._use_font("B", 18)
    pdf.set_text_color(30, 41, 59)
    pdf.multi_cell(0, 10, title)
    pdf.ln(4)

    # Metadata
    pdf._use_font("", 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 6, f"Date: {date_str}  |  Duration: {duration_str}  |  Language: {language.upper()}", 0, 1)
    pdf.ln(2)

    # Separator
    pdf.set_draw_color(226, 232, 240)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())
    pdf.ln(6)

    # Transcript section
    pdf._use_font("B", 14)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, "Transcript", 0, 1)
    pdf.ln(4)

    _add_transcript_to_pdf(pdf, transcript_text)

    # Analysis section
    if analysis_text:
        pdf.add_page()
        pdf._use_font("B", 14)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, "Analysis", 0, 1)
        pdf.ln(4)
        _add_markdown_to_pdf(pdf, analysis_text)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf


def _add_transcript_to_pdf(pdf: MeetingPDF, transcript_text: str):
    """Add transcript lines with colored timestamps."""
    lines = transcript_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        ts_match = re.match(
            r'(?:\*\*)?\[(\d{1,2}:\d{2}(?::\d{2})?)\](?:\s*([A-Z_0-9]+):?\*\*)?(.*)$',
            line,
        )
        if ts_match:
            ts_val = ts_match.group(1)
            speaker = ts_match.group(2) or ""
            text = ts_match.group(3).strip()

            # Timestamp in indigo
            pdf._use_font("", 9)
            pdf.set_text_color(99, 102, 241)
            ts_w = pdf.get_string_width(f"[{ts_val}] ") + 2
            pdf.cell(ts_w, 5, f"[{ts_val}] ", 0, 0)

            # Speaker
            if speaker:
                pdf._use_font("B", 10)
                pdf.set_text_color(30, 41, 59)
                spk_w = pdf.get_string_width(f"{speaker}: ") + 2
                pdf.cell(spk_w, 5, f"{speaker}: ", 0, 0)

            # Text
            pdf._use_font("", 10)
            pdf.set_text_color(51, 65, 85)
            remaining_w = pdf.epw - pdf.get_x() + pdf.l_margin
            if remaining_w < 30:
                pdf.ln(5)
            pdf.multi_cell(0, 5, text)
            pdf.ln(1)
        else:
            pdf._use_font("", 10)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 5, line)
            pdf.ln(1)


def _add_markdown_to_pdf(pdf: MeetingPDF, markdown_text: str):
    """Render basic markdown headings, lists, and paragraphs."""
    lines = markdown_text.strip().split("\n")
    for line in lines:
        line = line.rstrip()
        if not line:
            pdf.ln(3)
            continue

        if line.startswith("### "):
            pdf._use_font("B", 12)
            pdf.set_text_color(71, 85, 105)
            pdf.multi_cell(0, 6, line[4:].strip())
            pdf.ln(2)
        elif line.startswith("## "):
            pdf._use_font("B", 13)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 7, line[3:].strip())
            pdf.ln(2)
        elif line.startswith("# "):
            pdf._use_font("B", 15)
            pdf.set_text_color(30, 41, 59)
            pdf.multi_cell(0, 8, line[2:].strip())
            pdf.ln(3)
        elif line.startswith("---"):
            pdf.set_draw_color(226, 232, 240)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())
            pdf.ln(4)
        elif line.startswith("- ") or line.startswith("* "):
            pdf._use_font("", 10)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(8, 5, chr(8226), 0, 0)  # bullet
            pdf.multi_cell(0, 5, line[2:].strip())
            pdf.ln(1)
        elif re.match(r'^\d+\.\s', line):
            pdf._use_font("", 10)
            pdf.set_text_color(51, 65, 85)
            num = re.match(r'^(\d+\.)\s*', line).group(1)
            text = re.sub(r'^\d+\.\s*', '', line)
            pdf.cell(10, 5, num, 0, 0)
            pdf.multi_cell(0, 5, text)
            pdf.ln(1)
        else:
            pdf._use_font("", 10)
            pdf.set_text_color(51, 65, 85)
            clean = line.replace("**", "")
            pdf.multi_cell(0, 5, clean)
            pdf.ln(1)
```

- **Verification:** `python -c "from listener.export.pdf_export import export_pdf; print('PDF exporter OK')"`

---

### Task 4: Create SRT exporter

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/srt_export.py`
- **Action:** Create
- **Details:**

```python
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
            next_ts = m_next_ts = matches[i + 1].group(1)
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
```

- **Verification:** `python -c "from listener.export.srt_export import export_srt; print('SRT exporter OK')"`

---

### Task 5: Create JSON exporter

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/json_export.py`
- **Action:** Create
- **Details:**

```python
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
```

- **Verification:** `python -c "from listener.export.json_export import export_json; print('JSON exporter OK')"`

---

### Task 6: Add export endpoint to Flask app

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** Modify
- **Important:** Read this file FRESH before editing — it has been modified by features F1–F5, F7–F10.

Add the following new section **before** the `# Background processing` section (which starts around line 443). Insert it after the Live Transcription SSE section:

```python
# ---------------------------------------------------------------------------
# Multi-Format Export (F6)
# ---------------------------------------------------------------------------

@app.route("/api/export/<session_id>")
def api_export(session_id):
    """Export a session in the requested format (docx, pdf, srt, json)."""
    fmt = request.args.get("format", "").lower()
    if fmt not in ("docx", "pdf", "srt", "json"):
        return jsonify({"error": f"Unsupported format: {fmt}. Use: docx, pdf, srt, json"}), 400

    # Load session data
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    transcript_path = OUTPUT_DIR / f"{session_id}_transcript.md"
    analysis_path = OUTPUT_DIR / f"{session_id}_analysis.md"

    if not transcript_path.exists():
        return jsonify({"error": "Transcript not found"}), 404

    transcript_text = transcript_path.read_text()
    analysis_text = analysis_path.read_text() if analysis_path.exists() else None

    # Parse metadata
    title = f"Meeting {session_id}"
    duration_seconds = 0.0
    language = ""
    language_confidence = 0.0
    recipe_id = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            title = meta.get("title", title)
            duration_seconds = meta.get("duration", 0.0)
            language = meta.get("language", "")
            language_confidence = meta.get("language_probability", 0.0)
            recipe_id = meta.get("recipe_id", "") or ""
        except Exception:
            pass

    # Format display strings
    parts = session_id.split("_")
    date_str = parts[0] + " " + (parts[1] if len(parts) > 1 else "").replace("-", ":")
    duration_str = _fmt_duration(duration_seconds) if duration_seconds else ""

    from listener.export import EXPORTERS
    exporter = EXPORTERS[fmt]

    # Build kwargs — SRT and JSON accept extra params
    kwargs = dict(
        session_id=session_id,
        title=title,
        date_str=date_str,
        duration_str=duration_str,
        language=language,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
    )

    if fmt == "json":
        kwargs["duration_seconds"] = duration_seconds
        kwargs["language_confidence"] = language_confidence
        kwargs["recipe_id"] = recipe_id

    try:
        buf = exporter(**kwargs)
    except Exception as e:
        logger.error("Export failed for %s as %s: %s", session_id, fmt, e)
        return jsonify({"error": f"Export failed: {str(e)}"}), 500

    # MIME types and file extensions
    mime_map = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "srt": "text/srt; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    ext_map = {"docx": ".docx", "pdf": ".pdf", "srt": ".srt", "json": ".json"}

    filename = f"{session_id}{ext_map[fmt]}"
    return Response(
        buf.read(),
        mimetype=mime_map[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
```

- **Verification:** After adding, run: `python -c "from listener.web.app import app; print([r.rule for r in app.url_map.iter_rules() if 'export' in r.rule])"`

---

### Task 7: Add export CLI command

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** Modify
- **Important:** Read this file FRESH before editing.

Add the following new command section. Insert it **after** the `search` command (around line 114) and **before** the `record` command:

```python
# -----------------------------------------------------------------------
# listener export
# -----------------------------------------------------------------------

@cli.command("export")
@click.argument("session_id")
@click.option("--format", "-f", "fmt", required=True,
              type=click.Choice(["docx", "pdf", "srt", "json"], case_sensitive=False),
              help="Export format")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Directory containing session files (default: ./transcripts)")
@click.option("--output-file", default=None,
              help="Output file path (default: <session_id>.<format> in output-dir)")
def export_cmd(session_id, fmt, output_dir, output_file):
    """Export a meeting transcript in various formats.

    SESSION_ID is the session identifier (e.g., 2026-04-04_14-30-00).

    Examples:
        listener export 2026-04-04_14-30-00 -f pdf
        listener export 2026-04-04_14-30-00 -f docx -o ./exports
        listener export 2026-04-04_14-30-00 -f srt --output-file meeting.srt
    """
    import json as _json
    from listener.export import EXPORTERS

    out_dir = Path(output_dir)
    transcript_path = out_dir / f"{session_id}_transcript.md"
    analysis_path = out_dir / f"{session_id}_analysis.md"
    meta_path = out_dir / f"{session_id}_meta.json"

    if not transcript_path.exists():
        click.echo(f"Error: transcript not found at {transcript_path}", err=True)
        click.echo(f"Available sessions in {out_dir}:")
        if out_dir.exists():
            sessions = set()
            for f in out_dir.iterdir():
                if f.stem.endswith("_transcript"):
                    sessions.add(f.stem[:-11])  # strip _transcript
            for s in sorted(sessions):
                click.echo(f"  {s}")
        sys.exit(1)

    transcript_text = transcript_path.read_text()
    analysis_text = analysis_path.read_text() if analysis_path.exists() else None

    # Load metadata
    title = f"Meeting {session_id}"
    duration_seconds = 0.0
    language = ""
    language_confidence = 0.0
    recipe_id = ""
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text())
            title = meta.get("title", title)
            duration_seconds = meta.get("duration", 0.0)
            language = meta.get("language", "")
            language_confidence = meta.get("language_probability", 0.0)
            recipe_id = meta.get("recipe_id", "") or ""
        except Exception:
            pass

    parts = session_id.split("_")
    date_str = parts[0] + " " + (parts[1] if len(parts) > 1 else "").replace("-", ":")
    duration_str = _fmt_duration(duration_seconds) if duration_seconds else ""

    exporter = EXPORTERS[fmt.lower()]

    kwargs = dict(
        session_id=session_id,
        title=title,
        date_str=date_str,
        duration_str=duration_str,
        language=language,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
    )
    if fmt.lower() == "json":
        kwargs["duration_seconds"] = duration_seconds
        kwargs["language_confidence"] = language_confidence
        kwargs["recipe_id"] = recipe_id

    click.echo(f"Exporting {session_id} as {fmt.upper()}...")

    try:
        buf = exporter(**kwargs)
    except Exception as e:
        click.echo(f"Export failed: {e}", err=True)
        sys.exit(1)

    ext_map = {"docx": ".docx", "pdf": ".pdf", "srt": ".srt", "json": ".json"}
    if output_file:
        dest = Path(output_file)
    else:
        dest = out_dir / f"{session_id}{ext_map[fmt.lower()]}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.read())
    click.echo(f"Exported: {dest}")
```

- **Verification:** `python -m listener export --help`

---

### Task 8: Update pyproject.toml

- **File:** `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- **Action:** Modify

Add these two lines to the `dependencies` list (after the existing `noisereduce` line):

```
    "python-docx>=1.1.0",
    "fpdf2>=2.8.0",
```

The full dependencies list should look like:
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
    "pyyaml>=6.0",
    "noisereduce>=3.0.0",
    "python-docx>=1.1.0",
    "fpdf2>=2.8.0",
]
```

- **Verification:** `pip install -e /Users/alperencngzz/Desktop/listener && python -c "import docx; from fpdf import FPDF; print('OK')"`

---

### Task 9: Download DejaVu Sans fonts

- **File:** `/Users/alperencngzz/Desktop/listener/listener/export/fonts/DejaVuSans.ttf` and `DejaVuSans-Bold.ttf`
- **Action:** Create (download)
- **Details:**

```bash
mkdir -p /Users/alperencngzz/Desktop/listener/listener/export/fonts
cd /Users/alperencngzz/Desktop/listener/listener/export/fonts

# Download from dejavu-fonts GitHub releases
curl -L -o DejaVuSans.ttf "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
curl -L -o DejaVuSans-Bold.ttf "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
```

If GitHub raw URLs fail, try the alternative:
```bash
# Alternative: download the full release archive and extract
curl -L -o dejavu.zip "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip"
unzip -j dejavu.zip "*/DejaVuSans.ttf" "*/DejaVuSans-Bold.ttf" -d .
rm dejavu.zip
```

If ALL download attempts fail, the PDF exporter will still work — it falls back to Helvetica (with a warning for Turkish characters). This is acceptable.

- **Verification:** `ls -la /Users/alperencngzz/Desktop/listener/listener/export/fonts/`

---

## Frontend Changes

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Important:** Read this file FRESH before editing — it has been modified by many previous features.

### CSS Addition

Add the following CSS rules inside the `<style>` tag. A good place is after the `.dl-btn:hover` rule (around line 207):

```css
/* ---- Export dropdown ---- */
.export-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.export-dropdown{position:relative;display:inline-block}
.export-btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:7px 14px;border:1px solid #e2e8f0;border-radius:8px;
  font-size:12px;font-weight:600;color:#475569;background:#fff;
  cursor:pointer;transition:all .2s;
}
.export-btn:hover{background:#f1f5f9;border-color:#cbd5e1;color:#334155}
.export-btn svg{width:14px;height:14px}
.export-menu{
  display:none;position:absolute;top:100%;left:0;z-index:20;
  margin-top:4px;background:#fff;border:1px solid #e2e8f0;
  border-radius:10px;box-shadow:0 4px 12px rgba(0,0,0,.1);
  min-width:160px;padding:6px 0;
}
.export-menu.open{display:block}
.export-menu a{
  display:flex;align-items:center;gap:8px;
  padding:8px 14px;font-size:13px;color:#334155;
  text-decoration:none;transition:background .15s;
}
.export-menu a:hover{background:#f1f5f9}
.export-menu a .efmt{
  font-size:10px;font-weight:700;color:#6366f1;
  background:#eef2ff;padding:2px 6px;border-radius:4px;
  min-width:36px;text-align:center;
}
```

### HTML: Add export row to viewer

In the viewer section of the HTML, there is a `<div class="dl-row" id="dl-row"></div>` (around line 568). Add the following **immediately after** that line:

```html
<div class="export-row" id="export-row"></div>
```

### JavaScript Changes

**1. Add export icon constant.** Near the top of the `<script>` section where `DL_ICON` is defined, add:

```javascript
const EXPORT_ICON='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.5 2A1.5 1.5 0 003 3.5v13A1.5 1.5 0 004.5 18h11a1.5 1.5 0 001.5-1.5V7.621a1.5 1.5 0 00-.44-1.06l-4.12-4.122A1.5 1.5 0 0011.378 2H4.5zm4.75 11.25a.75.75 0 001.5 0v-2.546l.943.942a.75.75 0 101.06-1.06l-2.22-2.22a.75.75 0 00-1.06 0l-2.22 2.22a.75.75 0 001.06 1.06l.937-.938v2.542z" clip-rule="evenodd"/></svg>';
```

**2. Modify the `openViewer` function** to populate the export row. Find the line that sets `Q('#dl-row').innerHTML=dl;` (around line 833) and add the following lines **immediately after it**:

```javascript
  // Export buttons (F6)
  let exp='<div class="export-dropdown">';
  exp+=`<button class="export-btn" onclick="toggleExportMenu()">${EXPORT_ICON} Export As...</button>`;
  exp+='<div class="export-menu" id="export-menu">';
  exp+=`<a href="/api/export/${sess.id}?format=docx" download><span class="efmt">DOCX</span> Word Document</a>`;
  exp+=`<a href="/api/export/${sess.id}?format=pdf" download><span class="efmt">PDF</span> PDF Document</a>`;
  exp+=`<a href="/api/export/${sess.id}?format=srt" download><span class="efmt">SRT</span> Subtitles</a>`;
  exp+=`<a href="/api/export/${sess.id}?format=json" download><span class="efmt">JSON</span> Structured Data</a>`;
  exp+='</div></div>';
  Q('#export-row').innerHTML=exp;
```

**3. Add the `toggleExportMenu` function.** Add this function in the JavaScript section (near other viewer functions like `closeViewer`):

```javascript
function toggleExportMenu(){
  const m=Q('#export-menu');
  m.classList.toggle('open');
  // Close when clicking outside
  if(m.classList.contains('open')){
    setTimeout(()=>{
      const close=e=>{
        if(!m.contains(e.target)&&!e.target.closest('.export-btn')){
          m.classList.remove('open');
          document.removeEventListener('click',close);
        }
      };
      document.addEventListener('click',close);
    },0);
  }
}
```

**4. Update `closeViewer` function** to clean up export menu. Find the `closeViewer` function and add this line after the existing cleanup code (before `viewerSession=null`):

```javascript
  Q('#export-row').innerHTML='';
```

---

## Summary of All Files

| File | Action | Description |
|---|---|---|
| `listener/export/__init__.py` | **Create** | Export package init with EXPORTERS dict |
| `listener/export/docx_export.py` | **Create** | DOCX exporter using python-docx |
| `listener/export/pdf_export.py` | **Create** | PDF exporter using fpdf2 with DejaVu Unicode font |
| `listener/export/srt_export.py` | **Create** | SRT subtitle exporter (pure Python) |
| `listener/export/json_export.py` | **Create** | Structured JSON exporter (pure Python) |
| `listener/export/fonts/DejaVuSans.ttf` | **Create** | Unicode font for PDF (download) |
| `listener/export/fonts/DejaVuSans-Bold.ttf` | **Create** | Bold Unicode font for PDF (download) |
| `listener/web/app.py` | **Modify** | Add `/api/export/<session_id>` endpoint |
| `listener/web/templates/index.html` | **Modify** | Add export dropdown CSS, HTML, and JS |
| `listener/cli.py` | **Modify** | Add `listener export` CLI command |
| `pyproject.toml` | **Modify** | Add `python-docx>=1.1.0`, `fpdf2>=2.8.0` |

---

## Final Verification

Run these commands in order to verify end-to-end:

```bash
# 1. Check all imports work
python -c "from listener.export import EXPORTERS; print('Exporters:', list(EXPORTERS.keys()))"

# 2. Check CLI command exists
python -m listener export --help

# 3. Check Flask endpoint registered
python -c "
from listener.web.app import app
rules = [r.rule for r in app.url_map.iter_rules()]
assert '/api/export/<session_id>' in rules, 'Export endpoint not found'
print('Flask endpoint OK')
"

# 4. Test export with a real session (if transcripts exist)
cd /Users/alperencngzz/Desktop/listener
SESSIONS=$(ls transcripts/*_transcript.md 2>/dev/null | head -1 | sed 's|.*transcripts/||;s|_transcript.md||')
if [ -n "$SESSIONS" ]; then
  echo "Testing with session: $SESSIONS"
  python -m listener export "$SESSIONS" -f json
  python -m listener export "$SESSIONS" -f srt
  python -m listener export "$SESSIONS" -f docx
  python -m listener export "$SESSIONS" -f pdf
  echo "All exports completed!"
else
  echo "No sessions found — manual test needed after recording"
fi

# 5. Check web app starts
python -c "from listener.web.app import app; print('Web app OK')"

# 6. Verify fonts exist (optional — PDF works without them)
ls -la /Users/alperencngzz/Desktop/listener/listener/export/fonts/
```

Expected results:
- Step 1: `Exporters: ['docx', 'pdf', 'srt', 'json']`
- Step 2: Shows export command help with `--format` option
- Step 3: `Flask endpoint OK`
- Step 4: Creates `.json`, `.srt`, `.docx`, `.pdf` files in transcripts/
- Step 5: `Web app OK`
- Step 6: Shows DejaVuSans.ttf and DejaVuSans-Bold.ttf files
