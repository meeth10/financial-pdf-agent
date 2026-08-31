from src.extraction.pdf_router import _quality_score


def test_collapsed_financial_table_is_penalized():
    rows = [
        ["Cash and cash equivalents 100 80"],
        ["Total Assets 500 450"],
        ["Total Liabilities 300 270"],
    ]
    score, warnings = _quality_score(rows, 100.0)
    assert "collapsed_columns" in warnings
    assert score < 0.6


def test_structured_financial_table_scores_higher():
    rows = [
        ["Cash and cash equivalents", "100", "80"],
        ["Total Assets", "500", "450"],
        ["Total Liabilities", "300", "270"],
        ["Total Equity", "200", "180"],
    ]
    score, warnings = _quality_score(rows, 95.0)
    assert "collapsed_columns" not in warnings
    assert score > 0.65
