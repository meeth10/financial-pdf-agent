# Financial reasoning agent

The extraction layer remains the source of numbers. This agent sits above the structured SQLite store and answers questions using `src/agent/rules.yaml`.

## Model

Default model: `ornith:9b`.

Install/pull it once:

```bash
ollama pull ornith:9b
```

The agent uses the model for terminology, question interpretation, and tool selection. Arithmetic is performed by `derivation.py`, not by the model.

## Run

Assuming your populated store is `data/financials.db`:

```bash
python -m src.agent.run data/financials.db "What is EBITDA for FY2025?"
```

Examples:

```bash
python -m src.agent.run data/financials.db "What is net debt / EBITDA for FY2025?"
python -m src.agent.run data/financials.db "What is EBITDA margin for FY2025?"
python -m src.agent.run data/financials.db "What was revenue growth from FY2024 to FY2025?"
python -m src.agent.run data/financials.db "Calculate free cash flow for FY2025."
```

## Design

```text
question
  -> Ornith 9B
  -> deterministic retrieval tools
  -> rule-book derivation engine
  -> provenance + confidence
  -> concise answer
```

The agent must return `UNAVAILABLE` rather than inventing an input. Conflicting records return `CONFLICTED`. Filing-only EV/EBITDA remains unavailable until equity value is supplied.
