# financial-pdf-agent

A local-only agent for pulling balance sheet / income statement / cash
flow figures out of 10-Ks, SEBI filings, and investor decks, answering
questions against them with page-level citations, and exporting to an
Excel workbook (one tab per statement + a YoY summary).

Runs entirely on free tooling: Ollama + Hermes 4 14B for the agent
loop, Camelot + pdfplumber for extraction, SQLite for storage,
openpyxl for export.

## Why it's split into two phases

The agent does **not** re-read raw PDF text on every question. Ingestion
(extraction -> cleanup -> structured store) happens once per document,
offline. The agent loop only ever queries the structured store. This
is what makes `Source: Page X` in the answer format actually
trustworthy instead of something the model is asked to remember —
provenance is attached at ingestion time, not reconstructed at
question time. It's also what keeps most questions free of the LLM
entirely: `get_line_item` is a direct SQLite lookup.

```
PDF sources -> extraction layer -> structured store -> local agent -> validation -> answer / export
```

## What's tested vs. stubbed

Built and smoke-tested against a real (non-financial, but structurally
representative) table in this repo's dev environment:

- `extraction/pdf_router.py` — tested. Camelot lattice -> stream ->
  pdfplumber fallback, confidence-scored. On the test page, Camelot
  lattice found nothing (no ruling lines), Camelot **stream** correctly
  recovered a 3-column table at 97% self-reported accuracy, and
  pdfplumber's text strategy (the fallback of last resort) badly
  over-split words into garbage columns — so trust the confidence score,
  not just whichever extractor ran.
- `store/schema.py`, `store/db.py` — tested. Insert, deterministic
  lookup, period listing all verified.
- `export/excel_export.py` — tested. Produces a real .xlsx with per-
  statement tabs and a YoY tab from stored data.
- `ingest.py` — wiring tested end-to-end with `--skip-llm-cleanup`
  (no Ollama required for this dry run).
- `agent/tools.py`, `agent/run.py`, `extraction/llm_cleanup.py` —
  written against the documented Ollama tool-calling API, **not yet
  tested against a live model** — I don't have Ollama available in
  this environment. Test these first once you're running locally.
- `ingest.py`'s `METRIC_SYNONYMS` map is a 6-entry stub. This needs to
  grow once real filings are in hand — see "What I still need" below.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Camelot's lattice mode needs the system Ghostscript binary in addition
to the `ghostscript` PyPI package:

```bash
# macOS
brew install ghostscript
# Ubuntu/Debian
sudo apt install ghostscript
```

### Pull Hermes 4 14B

Not in the official Ollama library as a one-line pull — see
`Modelfile.hermes4-14b` for both the direct-from-Hugging-Face route and
the manual GGUF + Modelfile fallback. Short version:

```bash
ollama pull hf.co/bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M
ollama cp hf.co/bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M hermes4-14b
```

Q4_K_M is ~9GB — fits a 12-16GB card. Confirm tool-calling support
before relying on it:

```bash
ollama show hermes4-14b   # look for "tools" under Capabilities
```

## Usage

```bash
# 1. Ingest a document (page numbers = where the statement tables live)
python src/ingest.py path/to/filing.pdf \
  --entity "Acme Ltd" --doc-type sebi_annual --fiscal-year FY2025 \
  --period FY2025 --statement balance_sheet --pages 42,43 \
  --db data/financials.db

# 2. Ask a question
python -m src.agent.run data/financials.db "What was Acme's total debt in FY2025?"

# 3. Export everything for one entity
python -c "
from src.store.schema import init_db
from src.export.excel_export import export_entity
conn = init_db('data/financials.db')
export_entity(conn, 'Acme Ltd', 'output/acme_financials.xlsx')
"
```

## What I still need from you to take this from scaffold to working

1. **1-3 real sample PDFs** — ideally one 10-K, one SEBI filing, and
   one investor deck, whichever pages have the actual statement
   tables. Extraction logic for financial statements needs tuning
   against real layouts; the guide PDF I tested against has prose
   tables, not financial ones, so `MIN_CAMELOT_ACCURACY = 80` and the
   fallback order are a reasonable starting point, not a proven one
   for your actual documents.
2. **Hardware** — RAM/VRAM, so I know whether Q4_K_M is the right
   default quant or whether to size up/down, and whether an OCR
   fallback (for scanned older filings) is realistic to run locally
   alongside Hermes.
3. **Metric synonym list** — `ingest.py`'s `METRIC_SYNONYMS` dict is a
   stub. Once you have a real filing in hand, send over (or paste) the
   actual line-item labels used for debt, revenue, cash flow etc. so
   this can be built out properly rather than guessed.
4. **Scope** — is this for a fixed watchlist of companies you already
   track, or a generic "drop any filing in" tool? Changes whether
   `config.yaml`'s entity list should be enforced or just advisory.
5. Confirm the **Excel delivery** — saved locally is what's built now;
   say the word if you want it auto-emailed the way WAVE/FRAME/SIEVE
   already do, and I'll wire that in the same way.

## Repo layout

```
src/
  extraction/   pdf_router.py, llm_cleanup.py — PDF -> raw table rows -> cleaned rows
  store/        schema.py, db.py — SQLite line-item store with full provenance
  agent/        system_prompt.py, tools.py, run.py — the Ollama tool-calling loop
  export/       excel_export.py — per-statement tabs + YoY summary
  ingest.py     CLI: ties extraction -> cleanup -> store together
Modelfile.hermes4-14b   Ollama import instructions for Hermes 4 14B
config.yaml.example
```
