"""SQLite schema for the structured financial line-item store.

Design goal: every number the agent can answer with must carry enough
provenance to satisfy the retrieval agent's own validation rules —
period, entity, statement type, consolidated/standalone, scale, and an
exact page/table reference. The agent should never need to re-read raw
PDF text to answer "what page did this come from."
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity          TEXT NOT NULL,
    doc_type        TEXT NOT NULL,      -- 10K | annual_report | sebi_quarterly | sebi_annual | investor_deck
    fiscal_year     TEXT,               -- e.g. FY2025, or NULL if not yet known
    filepath        TEXT NOT NULL,
    ingested_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS line_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id           INTEGER NOT NULL REFERENCES documents(id),
    entity                TEXT NOT NULL,
    period                TEXT NOT NULL,       -- e.g. FY2025, Q2FY26
    statement             TEXT NOT NULL,       -- balance_sheet | income_statement | cash_flow
    metric                TEXT NOT NULL,       -- normalized: total_debt, revenue, operating_cash_flow, ...
    metric_raw            TEXT,                -- as it appeared in the document
    value                 REAL,
    unit                  TEXT,                -- e.g. "INR crore", "USD million"
    consolidated          INTEGER,             -- 1 = consolidated, 0 = standalone, NULL = unknown
    source_page           INTEGER,
    source_table          TEXT,                -- table caption/title if available
    extraction_method     TEXT,                -- camelot_stream | camelot_lattice | pdfplumber | ocr | llm_cleanup
    extraction_confidence REAL,                -- 0-1, from the extractor's own accuracy report where available
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_line_items_lookup
    ON line_items (entity, metric, period, statement);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
