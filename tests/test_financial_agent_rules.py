import sqlite3

from src.agent.derivation import calculate_metric, calculate_growth
from src.store.schema import SCHEMA


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO documents (entity, doc_type, fiscal_year, filepath) VALUES (?, ?, ?, ?)", ("TestCo", "annual_report", "FY2025", "test.pdf"))
    conn.commit()
    return conn


def add(conn, metric, value, period="FY2025", statement="income_statement", page=1, confidence=0.95):
    conn.execute(
        """INSERT INTO line_items (document_id, entity, period, statement, metric, metric_raw, value,
           unit, consolidated, source_page, source_table, extraction_method, extraction_confidence)
           VALUES (1, 'TestCo', ?, ?, ?, ?, ?, 'INR crore', 1, ?, 'test', 'test', ?)""",
        (period, statement, metric, metric, value, page, confidence),
    )
    conn.commit()


def test_ebitda_is_derived_deterministically():
    conn = make_conn()
    add(conn, "ebit", 1000)
    add(conn, "depreciation", 150)
    add(conn, "amortisation", 50)
    result = calculate_metric(conn, "TestCo", "EBITDA", "FY2025", "income_statement", True)
    assert result["status"] == "DERIVED"
    assert result["value"] == 1200
    assert result["confidence"] == "HIGH"


def test_direct_reported_value_beats_derived_value():
    conn = make_conn()
    add(conn, "ebitda", 1210)
    add(conn, "ebit", 1000)
    add(conn, "depreciation", 150)
    add(conn, "amortisation", 50)
    result = calculate_metric(conn, "TestCo", "EBITDA", "FY2025", "income_statement", True)
    assert result["status"] == "REPORTED"
    assert result["value"] == 1210


def test_negative_ebitda_multiple_is_unavailable():
    conn = make_conn()
    add(conn, "total_debt", 1000, statement="balance_sheet")
    add(conn, "cash_and_equivalents", 200, statement="balance_sheet")
    add(conn, "ebitda", -100)
    result = calculate_metric(conn, "TestCo", "net debt / EBITDA", "FY2025", consolidated=True)
    assert result["status"] == "UNAVAILABLE"


def test_growth_requires_positive_prior_period():
    conn = make_conn()
    add(conn, "revenue", 1200, period="FY2025")
    add(conn, "revenue", 1000, period="FY2024")
    result = calculate_growth(conn, "TestCo", "revenue", "FY2025", "FY2024", "income_statement", True)
    assert result["status"] == "DERIVED"
    assert round(result["value"], 2) == 20.0
