"""Deterministic financial derivation engine governed by rules.yaml.

The LLM may choose what to ask for, but this module owns arithmetic,
input requirements, status, and confidence propagation.
"""

from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any

import yaml

from src.store import db

RULES_PATH = Path(__file__).with_name("rules.yaml")


def load_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_RULES = load_rules()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def canonicalize_metric(metric: str) -> str:
    cleaned = _norm(metric)
    if cleaned in _RULES.get("formulas", {}):
        return cleaned
    for canonical, aliases in _RULES.get("canonical_terms", {}).items():
        if cleaned == canonical or cleaned in {_norm(a) for a in aliases}:
            return canonical
    return metric.strip().lower().replace(" ", "_")


def _lookup(conn, entity: str, metric: str, period: str, statement: str | None,
            consolidated: bool | None) -> dict[str, Any]:
    canonical = canonicalize_metric(metric)
    rows = db.get_line_item(conn, entity, canonical, period, statement, consolidated)
    if not rows:
        return {"status": "UNAVAILABLE", "metric": canonical, "period": period,
                "reason": "required input not found"}
    if len(rows) > 1:
        return {"status": "CONFLICTED", "metric": canonical, "period": period,
                "reason": "multiple matching line items", "candidates": [dict(r) for r in rows]}
    row = dict(rows[0])
    confidence = row.get("extraction_confidence") or 0.0
    level = "HIGH" if confidence >= 0.9 else "MEDIUM" if confidence >= 0.6 else "LOW"
    return {
        "status": "REPORTED", "metric": canonical, "value": row["value"],
        "unit": row["unit"], "period": row["period"], "statement": row["statement"],
        "consolidated": row["consolidated"], "source_page": row["source_page"],
        "source_table": row["source_table"], "confidence": level,
        "extraction_confidence": confidence,
    }


def _same_unit(inputs: list[dict[str, Any]]) -> bool:
    units = {str(x.get("unit") or "").strip().lower() for x in inputs}
    return len(units) <= 1


def _calc_expression(name: str, values: dict[str, float]) -> float | None:
    if name == "gross_profit":
        return values["revenue"] - values["cost_of_revenue"]
    if name == "ebitda":
        return values["ebit"] + values["depreciation"] + values["amortisation"]
    if name == "net_debt":
        return values["total_debt"] - values["cash_and_equivalents"]
    if name == "free_cash_flow":
        return values["operating_cash_flow"] - values["capital_expenditure"]
    if name == "ebitda_margin":
        return values["ebitda"] / values["revenue"] * 100
    if name == "ebit_margin":
        return values["ebit"] / values["revenue"] * 100
    if name == "gross_margin":
        return values["gross_profit"] / values["revenue"] * 100
    if name == "net_margin":
        return values["net_income"] / values["revenue"] * 100
    if name == "current_ratio":
        return values["current_assets"] / values["current_liabilities"]
    if name == "quick_ratio":
        return (values["cash_and_equivalents"] + values["short_term_investments"] + values["accounts_receivable"]) / values["current_liabilities"]
    if name == "debt_to_equity":
        return values["total_debt"] / values["shareholders_equity"]
    if name == "debt_to_ebitda":
        return values["total_debt"] / values["ebitda"]
    if name == "net_debt_to_ebitda":
        return values["net_debt"] / values["ebitda"]
    if name == "interest_coverage":
        return values["ebit"] / values["finance_cost"]
    if name == "fcf_margin":
        return values["free_cash_flow"] / values["revenue"] * 100
    if name == "cfo_to_ebitda":
        return values["operating_cash_flow"] / values["ebitda"] * 100
    if name == "cfo_to_pat":
        return values["operating_cash_flow"] / values["net_income"] * 100
    if name in {"revenue_growth", "ebitda_growth", "pat_growth"}:
        key = {"revenue_growth": "revenue", "ebitda_growth": "ebitda", "pat_growth": "net_income"}[name]
        return (values[f"{key}_current"] / values[f"{key}_prior"] - 1) * 100
    raise ValueError(f"Formula not implemented: {name}")


def _confidence_level(inputs: list[dict[str, Any]]) -> str:
    levels = [x.get("confidence") for x in inputs]
    if "LOW" in levels:
        return "LOW"
    if "MEDIUM" in levels:
        return "MEDIUM"
    return "HIGH"


def calculate_metric(conn, entity: str, metric: str, period: str,
                     statement: str | None = None,
                     consolidated: bool | None = None) -> dict[str, Any]:
    requested = canonicalize_metric(metric)

    # Direct retrieval always wins over a calculation.
    direct = _lookup(conn, entity, requested, period, statement, consolidated)
    if direct.get("status") == "REPORTED":
        return direct
    if requested not in _RULES.get("formulas", {}):
        return direct

    formula_spec = _RULES["formulas"][requested]
    inputs: list[dict[str, Any]] = []
    values: dict[str, float] = {}
    for input_metric in formula_spec["inputs"]:
        item = _lookup(conn, entity, input_metric, period, statement, consolidated)
        inputs.append(item)
        if item.get("status") != "REPORTED":
            return {
                "status": "UNAVAILABLE" if item.get("status") != "CONFLICTED" else "CONFLICTED",
                "metric": requested, "period": period,
                "reason": f"missing or ambiguous input: {input_metric}",
                "formula": formula_spec["expression"], "inputs": inputs,
            }
        values[input_metric] = float(item["value"])

    if not _same_unit(inputs) and formula_spec.get("unit") not in {"percent", "x"}:
        return {"status": "UNAVAILABLE", "metric": requested, "period": period,
                "reason": "inputs use incompatible units", "inputs": inputs}

    # Multiples with zero/negative EBITDA are not meaningful by policy.
    if requested in {"debt_to_ebitda", "net_debt_to_ebitda"} and values.get("ebitda", 1) <= 0:
        return {"status": "UNAVAILABLE", "metric": requested, "period": period,
                "reason": "EBITDA is zero or negative; leverage multiple is not meaningful",
                "formula": formula_spec["expression"], "inputs": inputs}

    # Ratio denominators must not be zero.
    for denom in ("revenue", "current_liabilities", "shareholders_equity", "finance_cost", "ebitda", "net_income"):
        if denom in values and denom in formula_spec["expression"] and values[denom] == 0:
            return {"status": "UNAVAILABLE", "metric": requested, "period": period,
                    "reason": f"denominator {denom} is zero", "formula": formula_spec["expression"],
                    "inputs": inputs}

    try:
        result = _calc_expression(requested, values)
    except (ZeroDivisionError, ValueError):
        return {"status": "UNAVAILABLE", "metric": requested, "period": period,
                "reason": "calculation could not be completed", "inputs": inputs}

    if result is None or not math.isfinite(result):
        return {"status": "UNAVAILABLE", "metric": requested, "period": period,
                "reason": "non-finite calculation result", "inputs": inputs}

    source_pages = {str(x.get("metric")): x.get("source_page") for x in inputs}
    return {
        "status": "DERIVED", "metric": requested, "value": round(result, 6),
        "unit": formula_spec.get("unit") or inputs[0].get("unit"),
        "period": period, "statement": statement,
        "consolidated": consolidated, "confidence": _confidence_level(inputs),
        "formula": formula_spec["expression"], "inputs": inputs,
        "source_pages": source_pages,
    }


def calculate_growth(conn, entity: str, metric: str, current_period: str,
                     prior_period: str, statement: str | None = None,
                     consolidated: bool | None = None) -> dict[str, Any]:
    base = canonicalize_metric(metric)
    mapping = {"revenue": "revenue_growth", "ebitda": "ebitda_growth", "net_income": "pat_growth"}
    growth_metric = mapping.get(base)
    if growth_metric is None:
        return {"status": "UNAVAILABLE", "metric": base,
                "reason": "growth rule not defined for this metric"}

    current = _lookup(conn, entity, base, current_period, statement, consolidated)
    prior = _lookup(conn, entity, base, prior_period, statement, consolidated)
    inputs = [current, prior]
    if any(x.get("status") != "REPORTED" for x in inputs):
        return {"status": "UNAVAILABLE", "metric": growth_metric,
                "reason": "current or prior value unavailable", "inputs": inputs}
    current_value = float(current["value"])
    prior_value = float(prior["value"])
    if prior_value == 0 or prior_value < 0:
        return {"status": "UNAVAILABLE", "metric": growth_metric,
                "reason": "prior-period denominator is zero or negative", "inputs": inputs}
    result = (current_value / prior_value - 1) * 100
    return {
        "status": "DERIVED", "metric": growth_metric, "value": round(result, 6),
        "unit": "%", "period": current_period,
        "formula": f"({current_value} / {prior_value} - 1) * 100",
        "inputs": inputs, "confidence": _confidence_level(inputs),
        "source_pages": {current_period: current.get("source_page"), prior_period: prior.get("source_page")},
    }
