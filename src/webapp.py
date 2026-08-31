"""Small local web UI for testing automatic financial statement extraction."""

from __future__ import annotations

import argparse
import sys
import json
import tempfile
from pathlib import Path

# Support both `python -m src.webapp` and `python src/webapp.py`.
# When executed as a script, Python puts `src/` on sys.path rather than
# the repository root, so the top-level `src` package is otherwise unavailable.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, jsonify, render_template_string, request

from src.auto_extract import extract_financial_statements

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Financial PDF Extractor</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;background:#f7f7f8;color:#111}
.card{background:#fff;border:1px solid #ddd;border-radius:14px;padding:22px;margin-bottom:18px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
button{background:#111;color:#fff;border:0;padding:11px 18px;border-radius:9px;cursor:pointer;font-weight:600}
input[type=file]{margin:12px 0;width:100%}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.tab{background:#eee;color:#111}.tab.active{background:#111;color:#fff}
pre{white-space:pre-wrap;background:#0e0e10;color:#ddd;border-radius:10px;padding:15px;overflow:auto;max-height:500px}
.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#eee;margin:3px;font-size:12px}
</style>
</head>
<body>
<h1>Financial PDF Extractor</h1>
<p>Upload an annual report. The system scans the entire PDF and finds the Balance Sheet, Income Statement and Cash Flow Statement automatically.</p>
<div class="card">
<form id="form"><input id="file" type="file" accept="application/pdf" required><button>Run extraction</button></form>
<div id="status"></div>
</div>
<div id="results"></div>
<script>
const form=document.getElementById('form'); const status=document.getElementById('status'); const results=document.getElementById('results');
form.addEventListener('submit', async e=>{
 e.preventDefault(); const file=document.getElementById('file').files[0]; if(!file)return;
 status.textContent='Scanning PDF and extracting statements...'; results.innerHTML='';
 const fd=new FormData(); fd.append('file',file);
 try { const r=await fetch('/extract',{method:'POST',body:fd}); const data=await r.json();
   if(!r.ok) throw new Error(data.error||'Extraction failed');
   render(data); status.textContent='Done.';
 } catch(err){ status.textContent='Error: '+err.message; }
});
function render(data){
 const names={balance_sheet:'Balance Sheet',income_statement:'Income Statement',cash_flow:'Cash Flow'};
 let html='<div class="card"><h2>'+escapeHtml(data.pdf.split('/').pop())+'</h2><div class="tabs">';
 Object.keys(names).forEach((k,i)=>html+=`<button class="tab ${i===0?'active':''}" onclick="showTab('${k}')">${names[k]}</button>`);
 html+='</div>';
 Object.entries(data.statements).forEach(([key,pages])=>{
   html+=`<section id="tab-${key}" class="statement" style="display:${key==='balance_sheet'?'block':'none'}"><h3>${names[key]}</h3>`;
   if(!pages.length){html+='<p>No candidate pages found.</p></section>';return;}
   pages.forEach(p=>{html+=`<div class="card"><b>Page ${p.page}</b> · discovery score ${p.score}`+(p.needs_ocr?' · <span class="badge">OCR likely</span>':'')+`<div>${p.matched_terms.map(x=>`<span class="badge">${escapeHtml(x)}</span>`).join('')}</div>`;
     p.tables.forEach((t,ti)=>{html+=`<p>Table ${ti+1} · ${escapeHtml(t.method)} · confidence ${(t.confidence*100).toFixed(1)}%</p><pre>${escapeHtml(JSON.stringify(t.rows,null,2))}</pre>`});
     html+='</div>';});
   html+='</section>';
 });
 html+='</div>'; results.innerHTML=html;
 window.showTab=k=>{document.querySelectorAll('.statement').forEach(x=>x.style.display='none'); document.getElementById('tab-'+k).style.display='block'; document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active')); [...document.querySelectorAll('.tab')].find(x=>x.textContent===names[k]).classList.add('active');}
}
function escapeHtml(s){return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
</script>
</body></html>
"""


@app.get("/")
def index():
    return render_template_string(HTML)


@app.post("/extract")
def extract():
    upload = request.files.get("file")
    if upload is None or not upload.filename.lower().endswith(".pdf"):
        return jsonify(error="Please upload a PDF file."), 400

    with tempfile.TemporaryDirectory(prefix="financial_pdf_") as tmp:
        path = Path(tmp) / Path(upload.filename).name
        upload.save(path)
        try:
            result = extract_financial_statements(str(path))
        except Exception as exc:
            return jsonify(error=str(exc)), 500
    return jsonify(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local financial PDF extraction UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=not args.no_debug)


if __name__ == "__main__":
    main()
