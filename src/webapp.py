"""Local analyst-style web UI for automatic financial statement extraction."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, jsonify, render_template_string, request

from src.auto_extract import extract_financial_statements

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Financial Data Workbench</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--ink:#111827;--muted:#687386;--line:#e3e7ee;--accent:#111827;--good:#087443;--warn:#a15c00;--bad:#b42318;--shadow:0 12px 35px rgba(15,23,42,.07)}
*{box-sizing:border-box} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
.app{max-width:1400px;margin:0 auto;padding:34px 26px 60px}.topbar{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:28px}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:700}.title{font-size:34px;line-height:1.1;margin:6px 0}.sub{color:var(--muted);max-width:760px;line-height:1.5}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}
.upload{padding:28px;border:1.5px dashed #b9c1ce;transition:.2s}.upload.drag{border-color:#111827;background:#fafafa}.upload-row{display:flex;justify-content:space-between;gap:20px;align-items:center}.filebox{display:flex;align-items:center;gap:16px}.file-icon{width:46px;height:46px;border-radius:12px;background:#111827;color:#fff;display:grid;place-items:center;font-weight:800}.filename{font-weight:650}.hint{font-size:13px;color:var(--muted);margin-top:4px}input[type=file]{display:none}.button{border:0;background:#111827;color:#fff;border-radius:11px;padding:12px 18px;font-weight:700;cursor:pointer}.button:disabled{opacity:.45;cursor:not-allowed}.secondary{background:#eef1f5;color:#111827}
#status{margin-top:16px;font-size:14px;color:var(--muted)}.progress{height:5px;background:#edf0f4;border-radius:100px;margin-top:10px;overflow:hidden;display:none}.progress i{display:block;height:100%;width:35%;background:#111827;border-radius:100px;animation:slide 1.1s infinite}@keyframes slide{0%{margin-left:-40%}100%{margin-left:105%}}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.metric{padding:18px 20px}.metric-label{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}.metric-value{font-size:27px;font-weight:750;margin-top:7px}.metric-foot{font-size:12px;color:var(--muted);margin-top:4px}
.tabs{display:flex;gap:8px;padding:8px;background:#eef1f5;border-radius:13px;margin:0 0 18px}.tab{flex:1;border:0;background:transparent;padding:12px;border-radius:9px;font-weight:700;cursor:pointer;color:var(--muted)}.tab.active{background:#fff;color:#111827;box-shadow:0 2px 8px rgba(15,23,42,.08)}
.statement{padding:22px}.statement-head{display:flex;justify-content:space-between;align-items:flex-start;gap:15px;margin-bottom:18px}.statement-title{font-size:22px;font-weight:750}.statement-desc{font-size:13px;color:var(--muted);margin-top:4px}.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{font-size:11px;border-radius:999px;padding:5px 8px;background:#f0f2f5;color:#5e6877;font-weight:650}.chip.good{background:#e9f7ef;color:var(--good)}.chip.warn{background:#fff3df;color:var(--warn)}.chip.bad{background:#fdecea;color:var(--bad)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:13px;margin-top:14px}.data-table{width:100%;border-collapse:collapse;min-width:650px;background:#fff}.data-table th,.data-table td{padding:10px 12px;border-bottom:1px solid #edf0f4;text-align:right;font-size:13px;white-space:nowrap}.data-table th:first-child,.data-table td:first-child{text-align:left;position:sticky;left:0;background:#fff}.data-table th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);background:#fafbfc}.data-table tr:last-child td{border-bottom:0}.data-table td.label{font-weight:560;max-width:380px;white-space:normal}
.candidates{margin-top:18px}.candidate{border:1px solid var(--line);border-radius:14px;margin-bottom:10px;overflow:hidden}.candidate summary{cursor:pointer;list-style:none;padding:14px 16px;display:flex;justify-content:space-between;gap:12px;align-items:center}.candidate summary::-webkit-details-marker{display:none}.candidate-body{padding:0 16px 16px;border-top:1px solid var(--line)}.preview{font-size:12px;color:var(--muted);line-height:1.5;white-space:pre-wrap;margin-top:10px}.raw{display:none}.raw pre{background:#111827;color:#e5e7eb;padding:18px;border-radius:13px;overflow:auto;font-size:12px;line-height:1.5}
.empty{padding:60px 20px;text-align:center;color:var(--muted)}.footer-note{margin-top:20px;color:var(--muted);font-size:12px;text-align:center}
@media(max-width:900px){.summary{grid-template-columns:repeat(2,1fr)}.upload-row,.topbar,.statement-head{align-items:flex-start;flex-direction:column}.button{width:100%}}
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <div><div class="eyebrow">Financial PDF Agent</div><div class="title">Financial Data Workbench</div><div class="sub">Upload an annual report and let the engine locate, extract and quality-check the Balance Sheet, Income Statement and Cash Flow automatically.</div></div>
    <button class="button secondary" id="rawBtn" style="display:none">View raw output</button>
  </div>

  <div class="panel upload" id="drop">
    <form id="form">
      <input id="file" type="file" accept="application/pdf" required>
      <div class="upload-row">
        <label class="filebox" for="file" style="cursor:pointer">
          <div class="file-icon">PDF</div>
          <div><div class="filename" id="filename">Choose an annual report</div><div class="hint">Drag & drop a PDF here, or click to browse</div></div>
        </label>
        <button class="button" id="run" type="submit">Analyze document</button>
      </div>
    </form>
    <div id="status"></div><div class="progress" id="progress"><i></i></div>
  </div>

  <div id="results" style="display:none"></div>
  <div class="footer-note">Extraction is evidence-first: parser confidence is separate from statement discovery confidence.</div>
</div>
<script>
const fileInput=document.getElementById('file'), fileName=document.getElementById('filename'), form=document.getElementById('form'), drop=document.getElementById('drop'), run=document.getElementById('run'), statusEl=document.getElementById('status'), progress=document.getElementById('progress'), results=document.getElementById('results'), rawBtn=document.getElementById('rawBtn');
let lastData=null;
fileInput.addEventListener('change',()=>{const f=fileInput.files[0]; fileName.textContent=f?f.name:'Choose an annual report';});
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',ev=>{const f=ev.dataTransfer.files[0];if(f&&f.type==='application/pdf'){fileInput.files=ev.dataTransfer.files;fileName.textContent=f.name}});
form.addEventListener('submit',async ev=>{ev.preventDefault();const file=fileInput.files[0];if(!file)return;run.disabled=true;statusEl.textContent='Scanning every page, ranking candidates and testing table structure…';progress.style.display='block';results.style.display='none';rawBtn.style.display='none';const fd=new FormData();fd.append('file',file);try{const r=await fetch('/extract',{method:'POST',body:fd});const data=await r.json();if(!r.ok)throw new Error(data.error||'Extraction failed');lastData=data;render(data);statusEl.textContent='Analysis complete.';rawBtn.style.display='inline-block'}catch(err){statusEl.textContent='Error: '+err.message}finally{run.disabled=false;progress.style.display='none'}});
rawBtn.addEventListener('click',()=>{document.querySelector('.raw').style.display=document.querySelector('.raw').style.display==='block'?'none':'block'});
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
function fmt(v){const s=String(v??'').trim();if(!s)return '';return esc(s)}
function qualityClass(q){if(q>=.78)return'good';if(q>=.5)return'warn';return'bad'}
function tableHtml(table){const rows=table.rows||[];if(!rows.length)return'<div class="empty">No usable table rows returned.</div>';const width=Math.max(...rows.map(r=>r.length));const normalized=rows.map(r=>Array.from({length:width},(_,i)=>r[i]??''));let header=normalized[0];const headerLooksNumeric=header.slice(1).some(x=>/^\(?[-+]?\d/.test(String(x).replaceAll(',','')));if(headerLooksNumeric)header=['Line item',...header.slice(1)];let body=headerLooksNumeric?normalized:normalized.slice(1);return '<div class="table-wrap"><table class="data-table"><thead><tr>'+header.map(x=>'<th>'+fmt(x)+'</th>').join('')+'</tr></thead><tbody>'+body.map(r=>'<tr>'+r.map((x,i)=>'<td class="'+(i===0?'label':'')+'">'+fmt(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function bestTable(p){return (p.tables||[]).slice().sort((a,b)=>(b.quality_score??b.confidence??0)-(a.quality_score??a.confidence??0))[0]}
function render(data){const names={balance_sheet:'Balance Sheet',income_statement:'Income Statement',cash_flow:'Cash Flow'};const statements=data.statements||{};let totalTables=0,totalPages=0,warnings=0;Object.values(statements).forEach(ps=>(ps||[]).forEach(p=>{totalPages++;totalTables+=(p.tables||[]).length;(p.tables||[]).forEach(t=>warnings+=(t.warnings||[]).length)}));let html=`<div class="summary"><div class="panel metric"><div class="metric-label">Document</div><div class="metric-value" style="font-size:18px;overflow:hidden;text-overflow:ellipsis">${esc(data.pdf.split('/').pop())}</div><div class="metric-foot">Analyzed automatically</div></div><div class="panel metric"><div class="metric-label">Statements</div><div class="metric-value">${Object.values(statements).filter(x=>(x||[]).length).length}/3</div><div class="metric-foot">Core financial statements</div></div><div class="panel metric"><div class="metric-label">Tables</div><div class="metric-value">${totalTables}</div><div class="metric-foot">Candidate extractions</div></div><div class="panel metric"><div class="metric-label">Warnings</div><div class="metric-value">${warnings}</div><div class="metric-foot">Review before modeling</div></div></div><div class="panel statement"><div class="tabs">${Object.keys(names).map((k,i)=>`<button class="tab ${i===0?'active':''}" data-tab="${k}">${names[k]}</button>`).join('')}</div>`;Object.entries(names).forEach(([key,name],idx)=>{const pages=statements[key]||[];html+=`<section data-section="${key}" style="display:${idx===0?'block':'none'}"><div class="statement-head"><div><div class="statement-title">${name}</div><div class="statement-desc">Top ranked pages and the strongest structurally validated extraction.</div></div><div class="chips"><span class="chip">${pages.length} candidate page${pages.length===1?'':'s'}</span></div></div>`;if(!pages.length){html+='<div class="empty">No candidate pages found.</div></section>';return}const bestPages=pages.slice(0,3);bestPages.forEach((p,pi)=>{const t=bestTable(p);const q=t?(t.quality_score??t.confidence??0):0;html+=`<details class="candidate" ${pi===0?'open':''}><summary><div><strong>Page ${p.page}</strong><div class="hint">Discovery score ${p.score} · ${p.needs_ocr?'OCR likely':'Text layer available'}</div></div><div class="chips"><span class="chip ${qualityClass(q)}">${t?Math.round(q*100)+'% extraction quality':'No usable table'}</span>${p.needs_ocr?'<span class="chip warn">OCR needed</span>':''}</div></summary><div class="candidate-body"><div class="chips">${(p.matched_terms||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')}${t?.method?`<span class="chip">${esc(t.method)}</span>`:''}${(t?.warnings||[]).map(x=>`<span class="chip warn">${esc(x)}</span>`).join('')}</div>${p.text_preview?`<div class="preview">${esc(p.text_preview)}</div>`:''}${t?tableHtml(t):'<div class="empty">No validated table on this candidate page.</div>'}</div></details>`});html+='</section>'});html+='</div><div class="raw"><pre>'+esc(JSON.stringify(data,null,2))+'</pre></div>';results.innerHTML=html;results.style.display='block';document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');document.querySelectorAll('[data-section]').forEach(x=>x.style.display=x.dataset.section===btn.dataset.tab?'block':'none')}));}
</script>
</body>
</html>
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
