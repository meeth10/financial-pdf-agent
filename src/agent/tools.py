"""Tools the agent can call. Kept deliberately thin: these wrap the
deterministic store (store/db.py) and the export module. No tool here
re-parses raw PDF text — that already happened at ingestion time.

Each function has a matching JSON schema in TOOL_SCHEMAS below, in the
format Ollama's /api/chat 'tools' parameter expects (same shape as
OpenAI function calling).
"""

import sqlite3
from store import db
from export.excel_export import export_entity


def get_line_item(conn: sqlite3.Connection, entity: str, metric: str, period: str,
                   statement: str | None = None, consolidated: bool | None = None) -> dict:
    rows = db.get_line_item(conn, entity, metric, period, statement, consolidated)
    if not rows:
        return {"status": "not_found", "entity": entity, "metric": metric, "period": period}
    if len(rows) > 1:
        # Ambiguous — usually consolidated vs standalone both present.
        # Surface both rather than silently picking one; this maps onto
        # the agent's own Step 7 (handle conflicts) rather than hiding it.
        return {"status": "ambiguous", "candidates": [dict(r) for r in rows]}
    row = dict(rows[0])
    return {
        "status": "REPORTED",
        "value": row["value"],
        "unit": row["unit"],
        "period": row["period"],
        "statement": row["statement"],
        "consolidated": bool(row["consolidated"]) if row["consolidated"] is not None else None,
        "source_page": row["source_page"],
        "source_table": row["source_table"],
        "confidence": (
            "HIGH" if (row["extraction_confidence"] or 0) >= 0.9 else
            "MEDIUM" if (row["extraction_confidence"] or 0) >= 0.6 else "LOW"
        ),
    }


def list_available_periods(conn: sqlite3.Connection, entity: str) -> dict:
    return {"entity": entity, "periods": db.list_periods(conn, entity)}


def list_available_metrics(conn: sqlite3.Connection, entity: str, statement: str | None = None) -> dict:
    return {"entity": entity, "statement": statement, "metrics": db.list_metrics(conn, entity, statement)}


def calculate_yoy(conn: sqlite3.Connection, entity: str, metric: str,
                   period_current: str, period_prior: str,
                   statement: str | None = None) -> dict:
    current = get_line_item(conn, entity, metric, period_current, statement)
    prior = get_line_item(conn, entity, metric, period_prior, statement)
    if current.get("status") != "REPORTED" or prior.get("status") != "REPORTED":
        return {"status": "insufficient_evidence", "current": current, "prior": prior}
    change = current["value"] - prior["value"]
    pct = (change / prior["value"] * 100) if prior["value"] else None
    return {
        "status": "DERIVED",
        "metric": metric,
        "period_current": period_current,
        "period_prior": period_prior,
        "value_current": current["value"],
        "value_prior": prior["value"],
        "change": round(change, 2),
        "change_pct": round(pct, 1) if pct is not None else None,
        "calculation": f"{current['value']} - {prior['value']} = {round(change, 2)}",
    }


def export_to_excel(conn: sqlite3.Connection, entity: str, output_path: str) -> dict:
    path = export_entity(conn, entity, output_path)
    return {"status": "ok", "path": path}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_line_item",
            "description": "Look up a single reported financial metric for an entity and period. Prefer this over any narrative search when the metric is a primary statement line item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "metric": {"type": "string", "description": "normalized metric name, e.g. total_debt, revenue, operating_cash_flow"},
                    "period": {"type": "string", "description": "e.g. FY2025, Q2FY26"},
                    "statement": {"type": "string", "enum": ["balance_sheet", "income_statement", "cash_flow"]},
                    "consolidated": {"type": "boolean"},
                },
                "required": ["entity", "metric", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_periods",
            "description": "List the fiscal periods available for an entity, to disambiguate before a lookup.",
            "parameters": {
                "type": "object",
                "properties": {"entity": {"type": "string"}},
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_metrics",
            "description": "List the normalized metric names available for an entity, optionally scoped to one statement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "statement": {"type": "string", "enum": ["balance_sheet", "income_statement", "cash_flow"]},
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_yoy",
            "description": "Compute a year-over-year (or period-over-period) change for a metric. Always returns status DERIVED, never REPORTED.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "metric": {"type": "string"},
                    "period_current": {"type": "string"},
                    "period_prior": {"type": "string"},
                    "statement": {"type": "string", "enum": ["balance_sheet", "income_statement", "cash_flow"]},
                },
                "required": ["entity", "metric", "period_current", "period_prior"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_excel",
            "description": "Export all stored statements and a YoY summary for an entity to an Excel workbook with one tab per statement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "output_path": {"type": "string"},
                },
                "required": ["entity", "output_path"],
            },
        },
    },
]

DISPATCH = {
    "get_line_item": get_line_item,
    "list_available_periods": list_available_periods,
    "list_available_metrics": list_available_metrics,
    "calculate_yoy": calculate_yoy,
    "export_to_excel": export_to_excel,
}
