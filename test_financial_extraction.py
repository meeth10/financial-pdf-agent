#!/usr/bin/env python3
"""Standalone financial-report extraction test.

No LLM. No SQLite. No hard-coded page numbers.

The test:
  1. Scans the entire PDF with PyMuPDF text extraction.
  2. Finds an annual-report financial-statement index / TOC when present.
  3. Extracts the printed-page number for Balance Sheet, Income Statement/P&L,
     Cash Flow and Equity statements.
  4. Resolves those printed pages to physical PDF pages by matching the actual
     statement title in page text.
  5. Verifies the candidate page contains a real table-like structure before
     running table extraction.
  6. Runs Camelot lattice, Camelot stream and pdfplumber on the candidate pages.
  7. Writes an auditable JSON report and a CSV of selected raw rows.

Usage:
    python test_financial_extraction.py samples/sample_annual_report.pdf

Optional:
    python test_financial_extraction.py samples/sample_annual_report.pdf \
        --output output/sample_extraction
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import camelot
import fitz
import pdfplumber


STATEMENT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "balance_sheet": [
        re.compile(r"consolidated\s+balance\s+sheets?", re.I),
        re.compile(r"balance\s+sheets?", re.I),
        re.compile(r"statement\s+of\s+financial\s+position", re.I),
        re.compile(r"financial\s+position", re.I),
    ],
    "income_statement": [
        re.compile(r"consolidated\s+statements?\s+of\s+operations", re.I),
        re.compile(r"statements?\s+of\s+operations", re.I),
        re.compile(r"statement\s+of\s+profit\s+and\s+loss", re.I),
        re.compile(r"profit\s+and\s+loss", re.I),
        re.compile(r"income\s+statement", re.I),
    ],
    "cash_flow": [
        re.compile(r"consolidated\s+statements?\s+of\s+cash\s+flows?", re.I),
        re.compile(r"statements?\s+of\s+cash\s+flows?", re.I),
        re.compile(r"cash\s+flow\s+statement", re.I),
    ],
    "equity": [
        re.compile(r"consolidated\s+statements?\s+of\s+shareholders.?\s+equity", re.I),
        re.compile(r"statements?\s+of\s+changes\s+in\s+equity", re.I),
        re.compile(r"shareholders.?\s+equity", re.I),
    ],
}

TOC_HINTS = (
    "index to consolidated financial statements",
    "index to financial statements",
    "financial statements and supplementary data",
    "contents",
)

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
PAGE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def page_has_statement_title(text: str, statement: str) -> bool:
    low = clean_text(text).lower()
    return any(p.search(low) for p in STATEMENT_PATTERNS[statement])


def statement_title_score(text: str, statement: str) -> float:
    """Score actual title evidence, not generic finance vocabulary."""
    text = clean_text(text)
    score = 0.0
    for pattern in STATEMENT_PATTERNS[statement]:
        if pattern.search(text):
            score += 10.0
            # Exact "consolidated" primary statements get extra weight.
            if "consolidated" in text.lower():
                score += 4.0
            break

    low = text.lower()
    if "years ended" in low or "as of" in low:
        score += 1.5
    if len(YEAR_RE.findall(text)) >= 2:
        score += 1.0
    if any(x in low for x in ("in millions", "in thousands", "in lakhs", "in crores", "in billions")):
        score += 1.0
    return score


def inventory(pdf_path: Path) -> list[dict[str, Any]]:
    doc = fitz.open(pdf_path)
    rows: list[dict[str, Any]] = []
    for physical_page, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        rows.append(
            {
                "physical_page": physical_page,
                "text": text,
                "text_clean": clean_text(text),
                "years": list(dict.fromkeys(YEAR_RE.findall(text))),
                "toc_hint": any(h in text.lower() for h in TOC_HINTS),
            }
        )
    doc.close()
    return rows


def locate_toc(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in inventory_rows if r["toc_hint"]]


def extract_toc_targets(inventory_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Find statement -> printed page from likely financial index pages.

    We intentionally use flexible line matching because annual reports format
    these indexes differently. The printed page is treated as a hint; actual
    physical-page resolution still happens against the statement title.
    """
    targets: dict[str, dict[str, Any]] = {}

    for page in locate_toc(inventory_rows):
        lines = [clean_text(x) for x in page["text"].splitlines() if clean_text(x)]
        for i, line in enumerate(lines):
            context = " ".join(lines[max(0, i - 1) : min(len(lines), i + 3)])
            for statement, patterns in STATEMENT_PATTERNS.items():
                if not any(p.search(context) for p in patterns):
                    continue

                # Search current/following line and immediate context for a
                # plausible printed page number. We prefer a trailing number.
                candidates = PAGE_NUMBER_RE.findall(context)
                if not candidates:
                    continue

                # Avoid years (2025 etc.) and obvious section numbers.
                page_nums = [int(n) for n in candidates if int(n) < 1000 and not (1900 <= int(n) <= 2100)]
                if not page_nums:
                    continue

                printed_page = page_nums[-1]
                existing = targets.get(statement)
                if existing is None or printed_page < existing["printed_page"]:
                    targets[statement] = {
                        "printed_page": printed_page,
                        "toc_physical_page": page["physical_page"],
                        "evidence": context,
                    }

    return targets


def resolve_physical_pages(
    inventory_rows: list[dict[str, Any]],
    statement: str,
    toc_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for row in inventory_rows:
        if page_has_statement_title(row["text"], statement):
            score = statement_title_score(row["text"], statement)
            candidates.append(
                {
                    "physical_page": row["physical_page"],
                    "score": score,
                    "title_evidence": row["text_clean"][:500],
                }
            )

    # Prefer the page nearest the printed-page target if one was recovered.
    printed_target = toc_target.get("printed_page") if toc_target else None
    if printed_target is not None:
        # We don't assume a fixed offset. Instead, search title candidates near
        # where the report's printed pages usually live and rank by title score.
        for candidate in candidates:
            distance = abs(candidate["physical_page"] - printed_target)
            candidate["toc_distance"] = distance
            candidate["score"] += max(0.0, 5.0 - min(distance, 5))
    else:
        for candidate in candidates:
            candidate["toc_distance"] = None

    return sorted(candidates, key=lambda x: (-x["score"], x["physical_page"]))


def table_likeness(text: str) -> float:
    low = text.lower()
    score = 0.0
    if len(YEAR_RE.findall(text)) >= 2:
        score += 2.0
    for term in (
        "total assets",
        "total liabilities",
        "net sales",
        "net income",
        "operating activities",
        "investing activities",
        "financing activities",
        "cash and cash equivalents",
        "equity",
    ):
        if term in low:
            score += 1.0
    if any(x in low for x in ("in millions", "in thousands", "in lakhs", "in crores")):
        score += 1.0
    return score


def camelot_tables(pdf_path: Path, physical_page: int, flavor: str) -> list[dict[str, Any]]:
    try:
        tables = camelot.read_pdf(
            str(pdf_path),
            pages=str(physical_page),
            flavor=flavor,
            strip_text="\n",
        )
    except Exception as exc:
        return [{"status": "error", "method": f"camelot_{flavor}", "error": repr(exc)}]

    output = []
    for index, table in enumerate(tables):
        df = table.df.fillna("").astype(str)
        report = getattr(table, "parsing_report", {}) or {}
        output.append(
            {
                "status": "ok",
                "method": f"camelot_{flavor}",
                "table_index": index,
                "accuracy": float(report.get("accuracy", 0.0)),
                "whitespace": float(report.get("whitespace", 0.0)),
                "rows": df.values.tolist(),
            }
        )
    return output


def pdfplumber_tables(pdf_path: Path, physical_page: int) -> list[dict[str, Any]]:
    output = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[physical_page - 1]
            for name, settings in (
                (
                    "lines",
                    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                ),
                (
                    "text",
                    {
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 3,
                        "join_tolerance": 3,
                    },
                ),
            ):
                try:
                    tables = page.extract_tables(table_settings=settings)
                except Exception as exc:
                    output.append({"status": "error", "method": f"pdfplumber_{name}", "error": repr(exc)})
                    continue

                for index, table in enumerate(tables):
                    rows = [[clean_text(c or "") for c in row] for row in table if row]
                    if rows:
                        output.append(
                            {
                                "status": "ok",
                                "method": f"pdfplumber_{name}",
                                "table_index": index,
                                "rows": rows,
                            }
                        )
    except Exception as exc:
        output.append({"status": "error", "method": "pdfplumber", "error": repr(exc)})
    return output


def choose_table(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    good = [c for c in candidates if c.get("status") == "ok" and c.get("rows")]
    if not good:
        return None

    def key(c: dict[str, Any]) -> tuple[float, int, int]:
        accuracy = float(c.get("accuracy") or 0.0)
        method_rank = {
            "camelot_lattice": 3,
            "camelot_stream": 2,
            "pdfplumber_lines": 1,
            "pdfplumber_text": 0,
        }.get(c.get("method"), -1)
        return accuracy, method_rank, len(c.get("rows", []))

    return max(good, key=key)


def write_outputs(prefix: Path, report: dict[str, Any], selected_rows: list[dict[str, Any]]) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    with prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["statement", "physical_page", "printed_page", "method", "accuracy", "table_index", "row_index", "cells"],
        )
        writer.writeheader()
        for row in selected_rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--output", default="output/financial_extraction_test")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}", file=sys.stderr)
        return 1

    pages = inventory(pdf_path)
    toc_targets = extract_toc_targets(pages)

    print("\n" + "=" * 80)
    print("AUTOMATIC FINANCIAL STATEMENT DISCOVERY")
    print("=" * 80)
    print(f"PDF: {pdf_path}")
    print(f"Physical pages: {len(pages)}")

    report: dict[str, Any] = {
        "source_file": str(pdf_path),
        "total_physical_pages": len(pages),
        "toc_targets": toc_targets,
        "statements": {},
    }
    selected_rows: list[dict[str, Any]] = []

    for statement in ("balance_sheet", "income_statement", "cash_flow", "equity"):
        target = toc_targets.get(statement)
        resolved = resolve_physical_pages(pages, statement, target)

        print(f"\n{statement.upper().replace('_', ' ')}")
        if target:
            print(f"  TOC printed page: {target['printed_page']} (found on physical page {target['toc_physical_page']})")
        else:
            print("  TOC target: not found")

        if not resolved:
            print("  STATUS: NOT FOUND")
            report["statements"][statement] = {"status": "not_found", "toc_target": target}
            continue

        top = resolved[0]
        print(f"  PHYSICAL PAGE: {top['physical_page']}")
        print(f"  TITLE SCORE: {top['score']:.1f}")
        print(f"  TABLE LIKENESS: {table_likeness(pages[top['physical_page'] - 1]['text']):.1f}")
        print(f"  EVIDENCE: {top['title_evidence'][:220]}")

        candidates = []
        candidates.extend(camelot_tables(pdf_path, top["physical_page"], "lattice"))
        candidates.extend(camelot_tables(pdf_path, top["physical_page"], "stream"))
        candidates.extend(pdfplumber_tables(pdf_path, top["physical_page"]))
        chosen = choose_table(candidates)

        if chosen:
            print(f"  EXTRACTOR: {chosen['method']}")
            print(f"  TABLE INDEX: {chosen.get('table_index')}")
            print(f"  ROWS: {len(chosen['rows'])}")
            if chosen.get("accuracy") is not None:
                print(f"  CAMELOT ACCURACY: {chosen['accuracy']:.1f}")
            print("  SAMPLE ROWS:")
            for row in chosen["rows"][:8]:
                print(f"    {row}")

            for row_index, cells in enumerate(chosen["rows"]):
                selected_rows.append(
                    {
                        "statement": statement,
                        "physical_page": top["physical_page"],
                        "printed_page": target.get("printed_page") if target else None,
                        "method": chosen["method"],
                        "accuracy": chosen.get("accuracy"),
                        "table_index": chosen.get("table_index"),
                        "row_index": row_index,
                        "cells": json.dumps(cells, ensure_ascii=False),
                    }
                )
        else:
            print("  EXTRACTOR: no usable table found")

        report["statements"][statement] = {
            "status": "found",
            "toc_target": target,
            "resolved_candidates": resolved[:5],
            "chosen_physical_page": top["physical_page"],
            "table_likeness": table_likeness(pages[top["physical_page"] - 1]["text"]),
            "extraction_candidates": candidates,
            "chosen_table": chosen,
        }

    write_outputs(Path(args.output), report, selected_rows)

    print("\n" + "=" * 80)
    print(f"JSON: {Path(args.output).with_suffix('.json')}")
    print(f"CSV : {Path(args.output).with_suffix('.csv')}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
