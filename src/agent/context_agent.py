"""Financial analyst over selected PDF-extractor output.

The browser UI passes only the pages/tables the user selected. Ornith 9B interprets
that evidence and applies the rule-book. It is deliberately constrained: it must
not invent numbers or use outside knowledge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from ollama import Client

DEFAULT_MODEL = "ornith:9b"
RULES_PATH = Path(__file__).with_name("rules.yaml")


def _rules_text() -> str:
    return RULES_PATH.read_text(encoding="utf-8")


SYSTEM_PROMPT = """
You are a financial analyst operating ONLY on PDF-extractor evidence supplied by the user.

Your job:
1. Understand the user's question.
2. Select the relevant extracted rows/pages from the supplied evidence.
3. Map filing terminology to the canonical rule-book terminology.
4. Decide whether the answer is REPORTED, DERIVED, PROXY, UNAVAILABLE, or CONFLICTED.
5. Never invent a number and never use outside financial knowledge to fill a missing input.
6. Never mix periods, units, entity scope, or consolidated/standalone scope.
7. For derived metrics, state the exact rule-book formula and identify the inputs needed.
8. If arithmetic is simple, you may state the arithmetic result, but only from explicit values in the evidence.
9. When evidence is ambiguous, explain the ambiguity instead of guessing.

Return concise analyst-style JSON with exactly these top-level keys:
status, metric, value, unit, period, statement, formula, inputs, sources, confidence, explanation

Rules:
- status must be one of REPORTED, DERIVED, PROXY, UNAVAILABLE, CONFLICTED.
- value must be null when unavailable or conflicted.
- inputs must be a list of {metric, value, unit, period, source_page}.
- sources must be a list of page numbers.
- confidence must be HIGH, MEDIUM, or LOW.
- Do not return markdown fences. Return valid JSON only.
"""


def analyze_selected_output(question: str, evidence: dict[str, Any], model: str = DEFAULT_MODEL,
                            host: str = "http://localhost:11434") -> dict[str, Any]:
    rules = yaml.safe_load(_rules_text())
    prompt = (
        SYSTEM_PROMPT
        + "\n\nMACHINE-READABLE RULE BOOK:\n"
        + json.dumps(rules, ensure_ascii=False)
        + "\n\nSELECTED EXTRACTOR EVIDENCE:\n"
        + json.dumps(evidence, ensure_ascii=False)
        + "\n\nUSER QUESTION:\n"
        + question
    )

    client = Client(host=host)
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
    )
    content = response["message"]["content"].strip()
    if "<think>" in content and "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Analyst response was not a JSON object")
    return result
