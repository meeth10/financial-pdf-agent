"""Per-page table extraction router.

Tested against a real borderless table (not a financial statement, but
representative of the problem): pdfplumber's default line-based
strategy returns nothing on borderless tables, and its text-based
strategy over-splits words into garbled columns. Camelot's *stream*
mode (whitespace-based, not just lattice/line-based) correctly
recovered the 3-column structure with a 97%+ self-reported accuracy
score. So the priority order here is:

    1. Camelot lattice  (bordered tables — most 10-K / SEBI statement pages)
    2. Camelot stream    (borderless tables — investor decks, some notes)
    3. pdfplumber        (fallback + fine-grained control for edge cases)
    4. OCR flag          (page returned near-empty text — likely scanned)

Camelot's own `parsing_report['accuracy']` is used as the confidence
score that flows into `extraction_confidence` in the store — this is
what lets the agent's Confidence: HIGH/MEDIUM/LOW field mean something
real instead of being guessed by the LLM.
"""

from dataclasses import dataclass
from typing import Optional

import pdfplumber


@dataclass
class ExtractedTable:
    page: int
    rows: list[list[str]]
    method: str            # camelot_lattice | camelot_stream | pdfplumber
    confidence: float      # 0-1
    table_caption: Optional[str] = None


MIN_CAMELOT_ACCURACY = 80.0   # below this, don't trust the table — try the next method
MIN_CHARS_FOR_TEXT_PAGE = 40  # below this, flag the page as likely scanned


def extract_page_tables(pdf_path: str, page_number: int) -> list[ExtractedTable]:
    """page_number is 1-indexed to match how humans (and your agent's
    'Source: Page X' output) refer to PDF pages."""
    results: list[ExtractedTable] = []

    # Camelot lattice first — cheap, and this is the common case for
    # bordered balance-sheet / income-statement tables.
    try:
        import camelot
        lattice = camelot.read_pdf(pdf_path, pages=str(page_number), flavor="lattice")
        for t in lattice:
            acc = t.parsing_report.get("accuracy", 0.0)
            if acc >= MIN_CAMELOT_ACCURACY:
                results.append(ExtractedTable(
                    page=page_number,
                    rows=t.df.values.tolist(),
                    method="camelot_lattice",
                    confidence=acc / 100.0,
                ))
    except Exception:
        pass  # ghostscript / no lines on page — fall through

    if not results:
        try:
            import camelot
            stream = camelot.read_pdf(pdf_path, pages=str(page_number), flavor="stream")
            for t in stream:
                acc = t.parsing_report.get("accuracy", 0.0)
                if acc >= MIN_CAMELOT_ACCURACY:
                    results.append(ExtractedTable(
                        page=page_number,
                        rows=t.df.values.tolist(),
                        method="camelot_stream",
                        confidence=acc / 100.0,
                    ))
        except Exception:
            pass

    if not results:
        # Last resort: pdfplumber with widened tolerances. This still
        # needs per-document tuning — see llm_cleanup.py for the pass
        # that repairs whatever comes out of here.
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_number - 1]
            settings = {"vertical_strategy": "text", "horizontal_strategy": "text",
                        "text_x_tolerance": 3, "text_y_tolerance": 3}
            for table in page.extract_tables(table_settings=settings):
                results.append(ExtractedTable(
                    page=page_number,
                    rows=table,
                    method="pdfplumber",
                    confidence=0.4,  # deliberately low — this path is unverified
                ))

    return results


def page_needs_ocr(pdf_path: str, page_number: int) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        text = page.extract_text() or ""
        return len(text.strip()) < MIN_CHARS_FOR_TEXT_PAGE
