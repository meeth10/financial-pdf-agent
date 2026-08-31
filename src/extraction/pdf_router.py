"""Robust financial-table extraction.

Camelot is treated as a candidate generator, not an oracle. Financial PDFs
regularly produce a 95-100% Camelot parsing score while still collapsing year
columns, merging numeric cells, or swallowing footnote text. We therefore run
multiple Camelot strategies, score the resulting table on financial structure,
and fall back to a coordinate-aware pdfplumber reconstruction when the table is
structurally weak.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
import re

import pdfplumber


@dataclass
class ExtractedTable:
    page: int
    rows: list[list[str]]
    method: str
    confidence: float
    table_caption: Optional[str] = None
    quality_score: float = 0.0
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


MIN_CHARS_FOR_TEXT_PAGE = 40
NUMERIC_RE = re.compile(r"^\(?[-+]?\s*\d[\d,]*(?:\.\d+)?%?\)?$")
FINANCIAL_TERMS = {
    "revenue", "sales", "income", "profit", "loss", "assets", "liabilities",
    "equity", "cash", "borrowings", "debt", "receivables", "payables",
    "inventory", "expenses", "tax", "ebit", "ebitda", "operating", "capital",
    "depreciation", "amortisation", "amortization", "dividend", "earnings",
}
TOTAL_TERMS = {"total", "subtotal", "net", "profit", "loss", "ebit", "ebitda"}


def _get_page_count(pdf_path: str) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def _validate_page(pdf_path: str, page_number: int) -> None:
    count = _get_page_count(pdf_path)
    if page_number < 1 or page_number > count:
        raise ValueError(f"Page {page_number} doesn't exist — this PDF has {count} page(s).")


def _clean_rows(rows) -> list[list[str]]:
    cleaned = []
    for row in rows or []:
        r = ["" if v is None else re.sub(r"\s+", " ", str(v)).strip() for v in row]
        while r and not r[-1]:
            r.pop()
        if any(r):
            cleaned.append(r)
    return cleaned


def _looks_numeric(value: str) -> bool:
    s = value.strip().replace("−", "-")
    if s in {"", "-", "—", "–", "N/A", "na"}:
        return s in {"-", "—", "–"}
    return bool(NUMERIC_RE.match(s.replace(" ", "")))


def _quality_score(rows: list[list[str]], method_score: float = 0.0) -> tuple[float, list[str]]:
    rows = _clean_rows(rows)
    warnings: list[str] = []
    if not rows:
        return 0.0, ["empty_table"]

    nonempty_widths = [sum(bool(x.strip()) for x in r) for r in rows]
    max_width = max(nonempty_widths, default=0)
    numeric_cells = sum(_looks_numeric(c) for r in rows for c in r if c.strip())
    numeric_rows = sum(sum(_looks_numeric(c) for c in r) >= 1 for r in rows)
    text = " ".join(c.lower() for r in rows for c in r)
    term_hits = sum(text.count(term) for term in FINANCIAL_TERMS)
    total_hits = sum(text.count(term) for term in TOTAL_TERMS)

    score = 0.0
    score += min(method_score / 100.0, 1.0) * 0.15
    score += min(max_width / 5.0, 1.0) * 0.20
    score += min(numeric_cells / max(len(rows) * 2, 1), 1.0) * 0.25
    score += min(numeric_rows / max(len(rows), 1), 1.0) * 0.15
    score += min(term_hits / 8.0, 1.0) * 0.20
    score += min(total_hits / 3.0, 1.0) * 0.05

    if max_width <= 1 and numeric_cells > 0:
        warnings.append("collapsed_columns")
        score -= 0.35
    if numeric_cells == 0:
        warnings.append("no_numeric_cells")
        score -= 0.25
    if numeric_rows < 2:
        warnings.append("too_few_numeric_rows")
        score -= 0.15

    return max(0.0, min(score, 1.0)), warnings


def _camelot_candidates(pdf_path: str, page_number: int) -> list[ExtractedTable]:
    import camelot

    candidates: list[ExtractedTable] = []
    strategies = [
        ("camelot_lattice", "lattice", {"line_scale": 40}),
        ("camelot_lattice_tight", "lattice", {"line_scale": 60, "process_background": True}),
        ("camelot_stream", "stream", {"row_tol": 8, "column_tol": 12, "split_text": True}),
        ("camelot_stream_tight", "stream", {"row_tol": 4, "column_tol": 8, "split_text": True}),
        ("camelot_stream_loose", "stream", {"row_tol": 10, "column_tol": 20, "split_text": True}),
    ]

    for label, flavor, kwargs in strategies:
        try:
            tables = camelot.read_pdf(pdf_path, pages=str(page_number), flavor=flavor, **kwargs)
        except Exception:
            continue
        for t in tables:
            raw_acc = float(t.parsing_report.get("accuracy", 0.0))
            rows = _clean_rows(t.df.values.tolist())
            q, warnings = _quality_score(rows, raw_acc)
            candidates.append(ExtractedTable(
                page=page_number,
                rows=rows,
                method=label,
                confidence=q,
                quality_score=q,
                warnings=warnings,
            ))
    return candidates


def _group_words_into_lines(words: list[dict], y_tol: float = 3.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        target = None
        for line in lines:
            avg_top = sum(w["top"] for w in line) / len(line)
            if abs(word["top"] - avg_top) <= y_tol:
                target = line
                break
        if target is None:
            lines.append([word])
        else:
            target.append(word)
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _coordinate_reconstruct(pdf_path: str, page_number: int) -> Optional[ExtractedTable]:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        words = page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False)

    if not words:
        return None

    lines = _group_words_into_lines(words)
    rows: list[list[str]] = []
    for line in lines:
        numeric = [w for w in line if _looks_numeric(w["text"])]
        if not numeric:
            continue

        first_num = min(w["x0"] for w in numeric)
        label_words = [w["text"] for w in line if w["x0"] < first_num - 4]
        label = " ".join(label_words).strip()
        if not label:
            continue

        values = [w["text"] for w in numeric]
        rows.append([label, *values])

    if len(rows) < 2:
        return None

    q, warnings = _quality_score(rows, 50.0)
    warnings.append("coordinate_reconstruction")
    return ExtractedTable(
        page=page_number,
        rows=rows,
        method="pdfplumber_coordinates",
        confidence=q,
        quality_score=q,
        warnings=warnings,
    )


def extract_page_tables(pdf_path: str, page_number: int) -> list[ExtractedTable]:
    _validate_page(pdf_path, page_number)

    candidates = _camelot_candidates(pdf_path, page_number)
    coord = _coordinate_reconstruct(pdf_path, page_number)
    if coord:
        candidates.append(coord)

    if not candidates:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_number - 1]
            settings_list = [
                {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                {"vertical_strategy": "text", "horizontal_strategy": "text", "text_x_tolerance": 2, "text_y_tolerance": 3},
            ]
            for settings in settings_list:
                try:
                    tables = page.extract_tables(table_settings=settings)
                except Exception:
                    continue
                for table in tables:
                    rows = _clean_rows(table)
                    q, warnings = _quality_score(rows, 40.0)
                    candidates.append(ExtractedTable(
                        page=page_number,
                        rows=rows,
                        method="pdfplumber",
                        confidence=q,
                        quality_score=q,
                        warnings=warnings,
                    ))

    if not candidates:
        return []

    candidates.sort(key=lambda t: (t.quality_score, t.confidence, len(t.rows)), reverse=True)
    best = candidates[0]

    unique: list[ExtractedTable] = [best]
    for candidate in candidates[1:]:
        if candidate.quality_score < max(best.quality_score - 0.15, 0.0):
            continue
        shape_a = (len(best.rows), max((len(r) for r in best.rows), default=0))
        shape_b = (len(candidate.rows), max((len(r) for r in candidate.rows), default=0))
        if shape_a != shape_b:
            unique.append(candidate)
        if len(unique) >= 2:
            break
    return unique


def page_needs_ocr(pdf_path: str, page_number: int) -> bool:
    _validate_page(pdf_path, page_number)
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        text = page.extract_text() or ""
        return len(text.strip()) < MIN_CHARS_FOR_TEXT_PAGE
