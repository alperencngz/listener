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
