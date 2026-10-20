#!/usr/bin/env python3
"""Summarize local recognition runs and create a local scan/text review index."""

import argparse
from collections import Counter
import json
from pathlib import Path

from ocr_sources import OUTPUT, PLAN, ROOT, now, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, help="Optional metadata-only JSON report, without source text")
    args = parser.parse_args()
    runs = []
    latest_files = list(OUTPUT.glob("*/latest.json")) + list((OUTPUT / "htr").glob("*/latest.json"))
    for latest in sorted(latest_files):
        folder = latest.parent / json.loads(latest.read_text(encoding="utf-8"))["run"]
        manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        records = []
        for page in manifest["expected_pages"]:
            path = folder / "pages" / f"{page:04d}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("fingerprint") == manifest["fingerprint"]:
                    records.append(record)
        scopes = Counter(r["scope"] for r in records)
        low = [r["page"] for r in records if r["statistics"].get("mean_word_confidence") is not None
               and r["statistics"]["mean_word_confidence"] < 60]
        runs.append({"id": manifest["job"]["id"], "engine": manifest["settings"].get("engine", "tesseract"),
                     "model": Path(manifest["settings"].get("model", "").replace("\\", "/")).name or manifest["settings"].get("languages"),
                     "device": manifest["settings"].get("device", "cpu"),
                     "gpu": manifest["settings"].get("gpu"),
                     "folder": folder.relative_to(OUTPUT).as_posix(),
                     "fingerprint": manifest["fingerprint"],
                     "expected_pages": len(manifest["expected_pages"]), "saved_pages": len(records),
                     "complete": len(records) == len(manifest["expected_pages"]),
                     "scope_counts": dict(scopes), "low_confidence_pages": low,
                     "partial_segmentation_pages": [r["page"] for r in records if r.get("partial_segmentation")],
                     "empty_pages": [r["page"] for r in records if not r["statistics"]["words"]],
                     "review_status": "unreviewed", "eligible_for_linguistic_analysis": False,
                     "errors": manifest.get("errors", []),
                     "pages": [{"page": r["page"], "scope": r["scope"], "statistics": r["statistics"],
                                "partial_segmentation": r.get("partial_segmentation", False)}
                               for r in records]})
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    pending = [{"id": j["id"], "status": "draft_htr_complete_requires_review"
                if any(r["id"] == j["id"] and r["engine"] == "kraken" and r["complete"] for r in runs)
                else j["status"], "notes": j["notes"]}
               for j in plan["jobs"] if j["status"] != "ready_for_draft_ocr"]
    report = {"schema_version": 1, "generated_at": now(), "integrity_check": "Run ocr_sources.py --verify and htr_sources.py --verify separately",
              "review_status": "unreviewed", "runs": runs, "pending_specialist_recognition": pending}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "report.json", report)
    (OUTPUT / "review.html").write_text(VIEWER, encoding="utf-8")
    if args.export:
        # Keep the tracked status report small and free of transcribed source material.
        public = {**report, "runs": [{k: v for k, v in run.items() if k != "pages"} for run in runs]}
        write_json(args.export, public)
    for r in runs:
        print(f"{r['id']} ({r['engine']}): {r['saved_pages']}/{r['expected_pages']} saved, UNREVIEWED")
    for p in pending:
        print(f"PENDING {p['id']}: {p['status']}")
    print("Local review: python3 -m http.server 8001 --directory data/ocr ; open /review.html")


VIEWER = '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Source OCR review</title>
<style>
body{font:16px system-ui;margin:1rem;background:#f6f3ec;color:#24221f}
nav{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:1rem 0}
select,button,input{font:inherit;padding:.4rem}input{width:6rem}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:1rem;align-items:start}
img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;
font:18px/1.7 "Noto Serif",serif;background:white;padding:1rem;max-height:85vh;overflow:auto}
#status{padding:.7rem;background:#fff0bc}a{color:#174c75}
@media(max-width:850px){.panes{grid-template-columns:1fr}}
</style>
<h1>Source OCR review</h1>
<p id="status">All recognition is unreviewed. Confidence scores do not measure accuracy.
Main text, variants and notes still require separation. No page is approved for linguistic analysis.</p>
<nav><label>Run <select id="run"></select></label><button id="prev">Previous</button>
<label>File page <input id="page" type="number" min="1"></label><button id="next">Next</button>
<label><input id="uncertain" type="checkbox" style="width:auto">Low word confidence only (Tesseract)</label></nav>
<p id="info"></p><p id="links"></p><div class="panes"><img id="scan" alt="Page preview"><pre id="text"></pre></div>
<script>
const byId=id=>document.getElementById(id);let runs=[],active,pages=[],index=0,request=0;
async function show(){
 const token=++request,p=pages[index];if(!p){byId('info').textContent='No saved pages in this selection.';
 byId('scan').removeAttribute('src');byId('text').textContent='';byId('links').replaceChildren();return}
 const stem=String(p.page).padStart(4,'0'),base=active.folder+'/pages/'+stem;
 byId('page').value=p.page;byId('scan').src=base+'.preview.jpg';
 const wordConfidence=p.statistics.mean_word_confidence;
 byId('info').textContent=p.scope+' | '+p.statistics.words+' words | mean '+
 (wordConfidence!==null?'word confidence '+wordConfidence:
 'character confidence '+(p.statistics.mean_character_confidence??'unavailable'))+' | UNREVIEWED'+
 (p.partial_segmentation?' | PARTIAL: manual line review required':'')+
 (p.statistics.words===0?' | NO TEXT RECOGNIZED: inspect original':'');
 byId('links').replaceChildren();
 for(const [label,suffix] of [['Text','.txt'],['Page metadata','.json'],['Preview','.preview.jpg']]){
  const a=document.createElement('a');a.href=base+suffix;a.textContent=label;
  byId('links').append(a,document.createTextNode('  '))}
 const combined=document.createElement('a');combined.href=active.folder+'/text.txt';
 combined.textContent='Combined draft';byId('links').append(combined);
 byId('text').textContent='Loading…';
 try{const response=await fetch(base+'.txt');if(!response.ok)throw Error('Cannot load page text');
 const text=await response.text();if(token===request)byId('text').textContent=text}
 catch(e){if(token===request)byId('text').textContent=e.message}
}
function select(){active=runs[byId('run').value];pages=active.pages.filter(p=>!byId('uncertain').checked||
 (p.statistics.mean_word_confidence!==null&&p.statistics.mean_word_confidence<60));index=0;show()}
byId('run').onchange=select;byId('uncertain').onchange=select;
byId('prev').onclick=()=>{index=Math.max(0,index-1);show()};
byId('next').onclick=()=>{index=Math.min(pages.length-1,index+1);show()};
byId('page').onchange=()=>{const n=Number(byId('page').value),i=pages.findIndex(p=>p.page===n);if(i>=0){index=i;show()}};
fetch('report.json').then(r=>{if(!r.ok)throw Error('Missing report.json');return r.json()}).then(report=>{
 runs=report.runs;for(const [i,r]of runs.entries()){const option=document.createElement('option');
 option.value=i;option.textContent=r.id+' ('+r.engine+', '+r.model+', '+r.saved_pages+'/'+r.expected_pages+')';
 byId('run').append(option)}if(runs.length)select();
}).catch(e=>byId('info').textContent=e.message+'. Serve data/ocr over localhost HTTP.');
</script></html>
'''


if __name__ == "__main__":
    main()
