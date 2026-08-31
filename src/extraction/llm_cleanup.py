"""LLM-assisted cleanup for extracted financial tables.

Financial numbers are immutable. Camelot/pdfplumber own the numbers; the LLM
is used only to clean/normalize labels. If the LLM fails, deterministic
parsing still returns the extracted rows.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ollama import Client

DEFAULT_MODEL = "ornith:9b"

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(?:\$|€|£|₹)?\s*"
    r"(?:\(\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*\)"
    r"|\(\s*\d+(?:\.\d+)?\s*\)"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?)"
)


def _clean_cell(cell: Any) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def _parse_number(token: str) -> float | int | None:
    token = token.strip().replace("$", "").replace("€", "").replace("£", "").replace("₹", "")
    negative = token.startswith("(") and token.endswith(")")
    token = token.strip("() ").replace(",", "")
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if negative:
        value = -value
    return int(value) if value.is_integer() else value


def _last_number(text: str) -> float | int | None:
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return None
    return _parse_number(matches[-1].group(0))


def _strip_numeric_tail(text: str) -> str:
    text = _clean_cell(text)
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return text
    label = text[:matches[-1].start()].strip(" $€£₹\t")
    return label.rstrip(" .:,-")


def _deterministic_rows(rows: list[list[str]]) -> list[dict]:
    parsed: list[dict] = []
    for row_index, row in enumerate(rows):
        cells = [_clean_cell(c) for c in (row or [])]
        if not cells:
            continue

        label = _strip_numeric_tail(cells[0])
        value = _last_number(cells[0])

        if value is None:
            for cell in cells[1:]:
                value = _last_number(cell)
                if value is not None:
                    break

        if not label:
            continue

        parsed.append({"row_id": row_index, "metric_raw": label, "value": value})
    return parsed


LABEL_PROMPT = """You clean labels from a financial statement table.

IMPORTANT:
- Do NOT change, infer, calculate, or reproduce any numeric value.
- Do NOT merge rows.
- Do NOT create rows.
- Preserve every row_id.
- Return ONLY a JSON array.
- Each object MUST be exactly:
  {\"row_id\": <integer>, \"metric_raw\": \"<cleaned label>\"}

Rows:
{rows}
"""


def _normalize_labels_with_llm(parsed: list[dict], model: str, host: str) -> dict[int, str]:
    if not parsed:
        return {}

    client = Client(host=host)
    payload = [{"row_id": r["row_id"], "metric_raw": r["metric_raw"]} for r in parsed]

    response = client.chat(
        model=model,
        messages=[{
            "role": "user",
            "content": LABEL_PROMPT.format(rows=json.dumps(payload, ensure_ascii=False)),
        }],
        options={"temperature": 0.0},
    )

    content = response["message"]["content"].strip()
    if "<think>" in content and "</think>" in content:
        content = content.split("</think>", 1)[1].strip()

    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("LLM cleanup response was not a JSON array")

    original_ids = {r["row_id"] for r in parsed}
    out: dict[int, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        row_id = item.get("row_id")
        label = item.get("metric_raw")
        if isinstance(row_id, int) and row_id in original_ids and isinstance(label, str) and label.strip():
            out[row_id] = label.strip()
    return out


def cleanup_table(rows: list[list[str]], model: str = DEFAULT_MODEL,
                  host: str = "http://localhost:11434") -> list[dict]:
    parsed = _deterministic_rows(rows)
    if not parsed:
        return []

    try:
        labels = _normalize_labels_with_llm(parsed, model=model, host=host)
    except Exception:
        labels = {}

    return [
        {"metric_raw": labels.get(r["row_id"], r["metric_raw"]), "value": r["value"], "unit": None}
        for r in parsed
    ]
