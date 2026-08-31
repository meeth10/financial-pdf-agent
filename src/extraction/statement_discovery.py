"""Discover financial-statement pages without requiring page numbers.

The detector is deliberately deterministic: it scans text from every page and
scores pages using statement-specific headings and accounting vocabulary. It
returns ranked candidates so callers can preserve traceability and inspect
ambiguous filings rather than hiding a bad decision inside an LLM prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Iterable

import pdfplumber


@dataclass(frozen=True)
class StatementCandidate:
    statement: str
    page: int
    score: float
    matched_terms: tuple[str, ...]
    text_preview: str


STATEMENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "balance_sheet": (
        r"balance sheet",
        r"statement of financial position",
        r"consolidated balance sheets?",
        r"consolidated statements? of financial position",
        r"assets\s+and\s+liabilities",
    ),
    "income_statement": (
        r"income statement",
        r"statement of (?:profit and loss|operations)",
        r"profit and loss account",
        r"consolidated statements? of (?:operations|income|profit and loss)",
        r"profit\s*&\s*loss",
        r"statement of comprehensive income",
    ),
    "cash_flow": (
        r"cash flow statement",
        r"statement of cash flows?",
        r"consolidated statements? of cash flows?",
        r"cash flows? from operating activities",
        r"cash flows? from investing activities",
        r"cash flows? from financing activities",
    ),
}

SUPPORT_TERMS: dict[str, tuple[str, ...]] = {
    "balance_sheet": (
        "total assets", "total liabilities", "shareholders' equity",
        "shareholders’ equity", "current assets", "current liabilities",
        "accounts receivable", "accounts payable",
    ),
    "income_statement": (
        "revenue", "net sales", "operating income", "gross profit",
        "profit before tax", "net income", "profit after tax", "ebitda",
    ),
    "cash_flow": (
        "operating activities", "investing activities", "financing activities",
        "net cash", "cash and cash equivalents", "capital expenditures",
    ),
}

STATEMENT_ORDER = ("balance_sheet", "income_statement", "cash_flow")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _score_page(text: str, statement: str) -> tuple[float, tuple[str, ...]]:
    normalised = _normalise(text)
    matched: list[str] = []
    score = 0.0

    for pattern in STATEMENT_PATTERNS[statement]:
        if re.search(pattern, normalised, flags=re.IGNORECASE):
            matched.append(pattern)
            score += 10.0

    for term in SUPPORT_TERMS[statement]:
        if term in normalised:
            matched.append(term)
            score += 1.5

    # A statement page generally contains several year columns / amounts.
    number_hits = len(re.findall(r"(?:\(?\s*[-$€£₹]?\s*[\d,]+(?:\.\d+)?\s*\)?)", text))
    score += min(number_hits, 20) * 0.15

    # Prefer pages whose text is substantial enough to contain a complete table.
    if len(normalised) >= 600:
        score += 2.0
    elif len(normalised) >= 250:
        score += 1.0

    return score, tuple(matched)


def discover_statement_pages(
    pdf_path: str,
    *,
    top_k: int = 3,
    min_score: float = 10.0,
) -> dict[str, list[StatementCandidate]]:
    """Scan every page and return ranked statement candidates."""
    candidates = {statement: [] for statement in STATEMENT_ORDER}

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                continue

            preview = " ".join(text.split())[:300]
            for statement in STATEMENT_ORDER:
                score, matched = _score_page(text, statement)
                if score >= min_score:
                    candidates[statement].append(
                        StatementCandidate(
                            statement=statement,
                            page=page_number,
                            score=round(score, 2),
                            matched_terms=matched,
                            text_preview=preview,
                        )
                    )

    for statement in STATEMENT_ORDER:
        candidates[statement].sort(key=lambda x: (-x.score, x.page))
        candidates[statement] = candidates[statement][:top_k]

    return candidates


def discover_pages(pdf_path: str, *, top_k: int = 3) -> dict[str, list[int]]:
    """Convenience API returning only page numbers."""
    discovered = discover_statement_pages(pdf_path, top_k=top_k)
    return {statement: [candidate.page for candidate in rows]
            for statement, rows in discovered.items()}


def candidates_as_dict(candidates: dict[str, Iterable[StatementCandidate]]) -> dict[str, list[dict]]:
    return {key: [asdict(item) for item in value] for key, value in candidates.items()}
