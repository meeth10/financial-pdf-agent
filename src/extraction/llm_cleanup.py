"""LLM cleanup pass for extracted tables.

Even a 97%-confidence Camelot extraction routinely wraps one logical
row across several physical rows (see the "Data" row in the smoke
test — it split across 3 rows because the source cell had wrapped
text). This pass hands the raw extracted rows to the local model and
asks it to (a) merge wrapped rows, (b) normalize metric names against
a supplied synonym list, and (c) return strict JSON.

This is intentionally the ONLY place in the ingestion pipeline that
calls the LLM — extraction and storage stay deterministic. Keep the
model's job narrow: given a small table snippet, restructure it. Not:
"read this whole document and tell me the debt."
"""

import json
from ollama import Client

DEFAULT_MODEL = "hermes4-14b"  # see README for import instructions

CLEANUP_PROMPT = """You are cleaning up a table that was auto-extracted from a financial \
PDF. Wrapped text often gets split across multiple physical rows. Merge rows that belong \
to the same logical entry, and return ONLY a JSON array of objects with this shape:

{{"metric_raw": "<label as it appeared>", "value": <number or null>, "unit": "<if stated>"}}

Do not invent values that are not present. If a cell is ambiguous, set value to null \
rather than guessing. Return JSON only, no prose, no markdown fences.

Raw extracted rows:
{rows}
"""


def cleanup_table(rows: list[list[str]], model: str = DEFAULT_MODEL,
                   host: str = "http://localhost:11434") -> list[dict]:
    client = Client(host=host)
    prompt = CLEANUP_PROMPT.format(rows=json.dumps(rows, ensure_ascii=False))
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
    )
    content = response["message"]["content"].strip()
    # Hermes 4 runs in hybrid reasoning mode and may wrap deliberation in
    # <think>...</think> before the answer — strip it if present.
    if "<think>" in content and "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Cleanup pass did not return valid JSON: {e}\nGot: {content[:500]}")
