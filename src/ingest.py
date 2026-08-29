"""Ingest one PDF: route each page's tables through extraction, clean
up the result with the LLM pass, and write normalized line items to
the store. This is intentionally a script you run once per document,
not something the chat agent triggers live.

TODO once you have real sample PDFs:
  - a metric-name synonym map (total_debt <- borrowings, financial
    liabilities, secured/unsecured loans, ...) to normalize metric_raw
    into `metric` before insert — currently that mapping is a stub.
  - period detection from column headers (FY2025 vs FY2024 vs the
    comparative prior-year column) — currently assumed to be passed
    in manually per document, since header formats vary a lot between
    10-Ks, SEBI quarterlies, and investor decks.
  - OCR fallback wiring for pages flagged by page_needs_ocr().
"""

import argparse
import sys

from extraction.pdf_router import extract_page_tables, page_needs_ocr
from extraction.llm_cleanup import cleanup_table
from store.schema import init_db
from store.db import add_document, add_line_item, LineItem

# Stub — replace with your real synonym map once you see actual filings.
METRIC_SYNONYMS = {
    "total borrowings": "total_debt",
    "borrowings": "total_debt",
    "revenue": "revenue",
    "revenue from operations": "revenue",
    "net cash from operating activities": "operating_cash_flow",
    "cash and cash equivalents": "cash",
}


def normalize_metric(raw_label: str) -> str | None:
    return METRIC_SYNONYMS.get(raw_label.strip().lower())


def ingest(pdf_path: str, entity: str, doc_type: str, fiscal_year: str,
           period: str, statement: str, pages: list[int], db_path: str,
           model: str = "hermes4-14b", skip_llm_cleanup: bool = False) -> None:
    conn = init_db(db_path)
    document_id = add_document(conn, entity, doc_type, fiscal_year, pdf_path)

    for page in pages:
        if page_needs_ocr(pdf_path, page):
            print(f"[page {page}] looks scanned — skipping, wire up OCR fallback first", file=sys.stderr)
            continue

        tables = extract_page_tables(pdf_path, page)
        if not tables:
            print(f"[page {page}] no table found by any extractor", file=sys.stderr)
            continue

        for table in tables:
            if skip_llm_cleanup:
                cleaned = [{"metric_raw": r[0], "value": None, "unit": None} for r in table.rows if r]
            else:
                try:
                    cleaned = cleanup_table(table.rows, model=model)
                except Exception as e:
                    print(f"[page {page}] cleanup failed: {e}", file=sys.stderr)
                    continue

            for row in cleaned:
                metric = normalize_metric(row.get("metric_raw", ""))
                if metric is None or row.get("value") is None:
                    continue  # unmapped or unparsed — leave out rather than guess
                add_line_item(conn, document_id, LineItem(
                    entity=entity, period=period, statement=statement,
                    metric=metric, metric_raw=row["metric_raw"], value=row["value"],
                    unit=row.get("unit") or "unspecified", consolidated=None,
                    source_page=page, source_table=table.table_caption,
                    extraction_method=table.method,
                    extraction_confidence=table.confidence,
                ))

    print(f"Ingested {pdf_path} for {entity} / {period} into {db_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("pdf_path")
    p.add_argument("--entity", required=True)
    p.add_argument("--doc-type", required=True, choices=["10K", "annual_report", "sebi_quarterly", "sebi_annual", "investor_deck"])
    p.add_argument("--fiscal-year", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--statement", required=True, choices=["balance_sheet", "income_statement", "cash_flow"])
    p.add_argument("--pages", required=True, help="comma-separated page numbers, e.g. 42,43,44")
    p.add_argument("--db", default="data/financials.db")
    p.add_argument("--model", default="hermes4-14b")
    p.add_argument("--skip-llm-cleanup", action="store_true", help="for a first dry run without Ollama running")
    args = p.parse_args()

    ingest(args.pdf_path, args.entity, args.doc_type, args.fiscal_year,
           args.period, args.statement, [int(x) for x in args.pages.split(",")],
           args.db, args.model, args.skip_llm_cleanup)
