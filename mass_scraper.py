#!/usr/bin/env python3
"""
RepeaterMock Mass Scraper — Download ALL free tests as interactive HTML.
No login needed. Guest mode only.

Usage:
    python mass_scraper.py                  # Scrape all
    python mass_scraper.py --max-tests 10   # Limit to 10 tests
    python mass_scraper.py --concurrency 3  # 3 parallel downloads
"""
import asyncio
import json
import os
import re
import sys
import time
import hashlib
from pathlib import Path
from playwright.async_api import async_playwright

# ─── Config ──────────────────────────────────────────────────────────
API_BASE = "https://api.repeatermock.com"
OUTPUT_DIR = Path(__file__).parent / "output"
PROGRESS_FILE = Path(__file__).parent / "progress.json"
CONCURRENCY = 5
MAX_RETRIES = 3
RATE_LIMIT_CAP = 120  # seconds

SERIES = [
    {"slug": "ssc-cgl", "name": "SSC CGL", "platform": "tb"},
    {"slug": "ssc-chsl", "name": "SSC CHSL 2026", "platform": "tb"},
    {"slug": "ssc-chsl-previous", "name": "SSC CHSL 2025", "platform": "tb"},
    {"slug": "ssc-cpo", "name": "SSC CPO 2026", "platform": "tb"},
    {"slug": "ssc-cpo-previous", "name": "SSC CPO 2025", "platform": "tb"},
    {"slug": "ssc-mts", "name": "SSC MTS 2026", "platform": "tb"},
    {"slug": "ssc-mts-previous", "name": "SSC MTS 2025", "platform": "tb"},
    {"slug": "ssc-gd-constable", "name": "SSC GD Constable", "platform": "tb"},
    {"slug": "ssc-selection-post", "name": "SSC Selection Post", "platform": "tb"},
    {"slug": "ssc-stenographer", "name": "SSC Stenographer", "platform": "tb"},
    {"slug": "ssc-je-ce", "name": "SSC JE Civil", "platform": "tb"},
    {"slug": "ssc-je-ee", "name": "SSC JE Electrical", "platform": "tb"},
    {"slug": "ssc-maths-previous-year-questions", "name": "SSC Maths PYP", "platform": "tb"},
    {"slug": "ssc-reasoning-previous-year-questions", "name": "SSC Reasoning PYP", "platform": "tb"},
    {"slug": "ssc-english-previous-year-questions", "name": "SSC English PYP", "platform": "tb"},
    {"slug": "ssc-gk-previous-year-questions", "name": "SSC GK PYP", "platform": "tb"},
    {"slug": "general-knowledge-ssc-railways-competitive-exams", "name": "Ace GK", "platform": "tb"},
    {"slug": "rrb-group-d", "name": "RRB Group D", "platform": "tb"},
    {"slug": "rrb-gk-previous-year-questions", "name": "RRB GK PYP", "platform": "tb"},
    {"slug": "rrb-general-science-previous-year-questions", "name": "RRB GS PYP", "platform": "tb"},
    {"slug": "sbi-po", "name": "SBI PO", "platform": "gd"},
]

# ─── Progress ─────────────────────────────────────────────────────────
def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"scraped": [], "failed": [], "series_cache": {}}

def save_progress(p):
    PROGRESS_FILE.write_text(json.dumps(p, indent=2, ensure_ascii=False))

# ─── RSC payload extraction ──────────────────────────────────────────
def extract_flight_payload(html):
    payload = ""
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html):
        chunk = m.group(1)
        chunk = chunk.replace('\\n','\n').replace('\\r','\r').replace('\\"','"').replace('\\\\','\\')
        payload += chunk
    return payload

def thorough_unescape(text):
    if not text: return ""
    result = text
    for _ in range(3):
        tmp = __import__('html').unescape(result)
        if tmp == result: break
        result = tmp
    return result

def extract_json_object(payload, key):
    search = f'"{key}":{{'
    idx = payload.find(search)
    if idx < 0: return None
    start = payload.find('{', idx + len(key) + 2)
    depth = 0; in_str = False; esc = False
    for j in range(start, len(payload)):
        c = payload[j]
        if esc: esc = False; continue
        if c == '\\': esc = True; continue
        if c == '"': in_str = not in_str; continue
        if in_str: continue
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try: return json.loads(payload[start:j+1])
                except: return None
    return None

def parse_questions(payload):
    questions = []
    search_str = '{"isNum":'
    idx = 0
    while True:
        idx = payload.find(search_str, idx)
        if idx < 0: break
        depth = 0; in_str = False; esc = False; start = idx
        for j in range(idx, len(payload)):
            c = payload[j]
            if esc: esc = False; continue
            if c == '\\': esc = True; continue
            if c == '"': in_str = not in_str; continue
            if in_str: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try: questions.append(json.loads(payload[start:j+1]))
                    except: pass
                    break
        idx += 1
    return questions

def clean_question(q):
    qid = q.get("_id") or q.get("id") or ""
    text_en = ""; options = []
    if q.get("text"):
        if isinstance(q["text"].get("en"), str): text_en = q["text"]["en"]
        elif isinstance(q["text"].get("en"), dict): text_en = q["text"]["en"].get("value", "")
        for o in q["text"].get("options", []):
            if isinstance(o, str): options.append(o)
            elif isinstance(o, dict): options.append(o.get("value", o.get("en", "")))
    elif q.get("en"):
        if isinstance(q["en"], dict):
            text_en = q["en"].get("value", "")
            for o in q["en"].get("options", []):
                if isinstance(o, dict): options.append(o.get("value", ""))
                elif isinstance(o, str): options.append(o)
    return {"id": qid, "marks": q.get("posMarks",2), "negMarks": q.get("negMarks",0.5),
            "textEn": thorough_unescape(text_en), "options": [thorough_unescape(o) for o in options]}

def build_rsc_ref_table(payload):
    """Build lookup: $N hex ID → actual solution text."""
    refs = {}
    for m in re.finditer(r'([0-9a-f]+):T[0-9a-f]+,', payload):
        ref_id = m.group(1)
        content_start = m.end()
        remaining = payload[content_start:]
        # Find end of content
        min_end = len(remaining)
        for ep in [r'"\]\)', r'\n[0-9a-f]+:T', r',"\]\)']:
            em = re.search(ep, remaining)
            if em and em.start() < min_end:
                min_end = em.start()
        content = remaining[:min_end]
        content = content.replace('\\n','\n').replace('\\r','\r').replace('\\"','"').replace('\\\\','\\')
        content = thorough_unescape(content)
        if content and len(content) > 5:
            refs[ref_id] = content
    return refs

def resolve_solution(ans, ref_table):
    if not ans or not ans.get("sol"): return ""
    en = ans["sol"].get("en", {})
    val = thorough_unescape(en.get("value", "")) if isinstance(en, dict) else thorough_unescape(str(en))
    m = re.match(r'^\$(\w+)$', val.strip())
    if m:
        return ref_table.get(m.group(1), "")
    if not val or len(val.strip()) < 5:
        return ""
    return val

def get_correct(ans):
    if not ans: return 0
    c = ans.get("correctOption")
    if isinstance(c, str):
        try: return int(c)
        except: return 0
    return c or 0

# ─── HTML template ──────────────────────────────────────────────────
def generate_html(questions, answers, analysis, ref_table, title):
    qs_data = []
    sol_count = 0
    for q in questions:
        ans = answers.get(q["id"], {}) if answers else {}
        sol = resolve_solution(ans, ref_table)
        if sol: sol_count += 1
        qs_data.append({"text": q["textEn"], "options": q["options"], "correct": get_correct(ans),
                       "solution": sol, "marks": q.get("marks",2), "negMarks": q.get("negMarks",0.5)})

    ana_data = {}
    if analysis:
        ts = analysis.get("ts",{}); an = analysis.get("analysis",{})
        ana_data = {"rank": ts.get("rank"), "percentile": ts.get("percentile"),
                    "avgMarks": an.get("avgMarks"), "totalStudents": an.get("totalStudents")}

    qs_json = json.dumps(qs_data, ensure_ascii=False)
    ana_json = json.dumps(ana_data)
    total_q = len(questions)

    return '''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>''' + title + '''</title>
<script>window.MathJax={tex:{inlineMath:[['\\\\(','\\\\)']],displayMath:[['\\\\[','\\\\]']]},svg:{fontCache:'global'},options:{skipHtmlTags:['script','noscript','style','textarea','pre','code']}};const QUESTIONS=''' + qs_json + ''';const ANALYSIS=''' + ana_json + ''';</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@4.1.0/tex-mml-chtml.js" async></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',-apple-system,sans-serif;background:#f8f9fa;color:#1a1a2e;line-height:1.7}.container{max-width:800px;margin:0 auto;padding:16px}
.navbar{background:#1a1a2e;color:#fff;padding:12px 20px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.15)}.navbar .logo{font-weight:800;font-size:18px;color:#4ade80}.navbar .timer{font-size:18px;font-weight:700;color:#facc15}.navbar .nav-actions{display:flex;gap:12px;align-items:center}.submit-btn{background:#dc2626;color:#fff;border:0;padding:8px 20px;border-radius:8px;font-weight:700;cursor:pointer;font-size:14px;display:none}.submit-btn.visible{display:inline-block}.view-solutions{background:#2563eb;color:#fff;border:0;padding:8px 20px;border-radius:8px;font-weight:700;cursor:pointer;font-size:14px}
.test-header{background:#fff;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.06)}.test-header h1{font-size:22px;margin-bottom:8px}.badge{display:inline-block;background:#e0e7ff;color:#4338ca;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-right:8px}
.score-banner{background:linear-gradient(135deg,#4338ca,#7c3aed);color:#fff;border-radius:12px;padding:24px;margin:16px 0;text-align:center;display:none}.score-banner.visible{display:block}.score-banner .score{font-size:48px;font-weight:800}.score-banner .score-label{font-size:14px;opacity:.9;margin-top:4px}
.analysis-card{background:#fff;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.06);display:none}.analysis-card.visible{display:block}.analysis-card h2{color:#4338ca;margin-bottom:16px}.analysis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.stat{text-align:center;padding:16px;border-radius:10px;background:#f3f4f6}.stat .value{font-size:32px;font-weight:800}.stat .label{font-size:11px;color:#6b7280;text-transform:uppercase;margin-top:4px}.rank{color:#4338ca}.pct{color:#ec4899}.avg{color:#10b981}.total{color:#f59e0b}
.question-card{background:#fff;border-radius:12px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden}.q-header{background:#f9fafb;padding:12px 20px;border-bottom:1px solid #e5e7eb;display:flex;justify-content:space-between;align-items:center}.q-number{font-weight:700;color:#4338ca;font-size:14px}.q-marks{font-size:12px;color:#6b7280}.pos{color:#10b981;font-weight:600}.neg{color:#ef4444}.q-body{padding:20px}.q-text{font-size:15px;margin-bottom:16px}.q-text p{margin-bottom:8px}.q-text img{max-width:100%;border-radius:8px;margin:8px 0;display:block}
.options{margin-left:4px}.option{display:flex;align-items:flex-start;padding:12px 16px;margin-bottom:8px;border-radius:10px;border:2px solid #e5e7eb;cursor:pointer;transition:all .15s}.option:hover{border-color:#c7d2fe;background:#f0f4ff}.option.selected{border-color:#4338ca;background:#eef2ff}.option.correct{border-color:#10b981;background:#d1fae5}.option.wrong{border-color:#ef4444;background:#fee2e2}.option-letter{font-weight:700;margin-right:12px;min-width:20px}.option.correct .option-letter{color:#059669}.option.wrong .option-letter{color:#dc2626}.option.selected .option-letter{color:#4338ca}.option-text{flex:1;font-size:14px}.option-text img{max-width:100%;border-radius:4px}.option-check{margin-left:auto;font-size:20px}.option.correct .option-check{color:#10b981}
.solution{margin-top:16px;padding:16px;background:#f0f9ff;border-radius:10px;border-left:4px solid #0ea5e9;display:none}.solution.visible{display:block}.solution-header{display:flex;align-items:center;gap:8px;margin-bottom:12px}.solution-icon{background:#0ea5e9;color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px}.solution-title{font-weight:700;color:#0ea5e9;font-size:14px}.solution-content{font-size:14px;color:#1e3a5f;line-height:1.8}.solution-content p{margin-bottom:8px}.solution-content img{max-width:100%;border-radius:8px;margin:8px 0;display:block}
.palette{position:fixed;right:20px;top:80px;background:#fff;border-radius:12px;padding:12px;box-shadow:0 4px 12px rgba(0,0,0,.1);z-index:50;max-width:200px}.palette-title{font-size:12px;color:#6b7280;margin-bottom:8px;text-transform:uppercase;font-weight:600}.palette-grid{display:grid;grid-template-columns:repeat(10,1fr);gap:4px}.palette-item{width:24px;height:24px;border-radius:6px;background:#e5e7eb;display:flex;align-items:center;justify-content:center;font-size:10px;cursor:pointer;border:1px solid #d1d5db}.palette-item.answered{background:#10b981;color:#fff;border-color:#059669}
@media(max-width:768px){.palette{display:none}.analysis-grid{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<div class="navbar"><div class="logo">RepeaterMock</div><div class="nav-actions"><div class="timer" id="timer">⏱ 60:00</div><button class="submit-btn" id="submitBtn" onclick="submitTest()">Submit Test</button><button class="view-solutions" id="vsBtn" onclick="toggleSolutions()" style="display:none;">📖 Hide Solutions</button></div></div>
<div class="container"><div class="test-header"><h1>''' + title + '''</h1><div style="font-size:14px;color:#6b7280"><span class="badge">📋 ''' + str(total_q) + ''' Q</span><span class="badge">⏱ 60 min</span><span class="badge">✅ Guest</span><span class="badge">💡 ''' + str(sol_count) + ''' Sol</span></div></div>
<div class="score-banner" id="scoreBanner"><div class="score" id="scoreValue">0/''' + str(total_q) + '''</div><div class="score-label" id="scoreLabel">Score</div></div>
<div class="analysis-card" id="analysisCard"><h2>📈 Analysis</h2><div class="analysis-grid" id="analysisGrid"></div></div>
<div id="questions"></div></div>
<div class="palette"><div class="palette-title">Palette</div><div class="palette-grid" id="palette"></div></div>
<script>
var totalQ=QUESTIONS.length,userAnswers={},submitted=false,timeLeft=3600,solsVisible=true;
var qC=document.getElementById('questions');
QUESTIONS.forEach(function(q,i){var o='';q.options.forEach(function(opt,oi){o+='<div class="option" data-option="'+(oi+1)+'" onclick="selectOption('+i+','+(oi+1)+')"><div class="option-letter">'+String.fromCharCode(65+oi)+'</div><div class="option-text">'+opt+'</div><div class="option-check"></div></div>'});var s='';if(q.solution&&q.solution.length>5){s='<div class="solution" id="sol'+i+'"><div class="solution-header"><div class="solution-icon">💡</div><div class="solution-title">Solution</div></div><div class="solution-content">'+q.solution+'</div></div>'}qC.innerHTML+='<div class="question-card" id="q'+i+'" data-correct="'+q.correct+'"><div class="q-header"><div class="q-number">Q'+(i+1)+'</div><div class="q-marks"><span class="pos">+'+q.marks+'</span> <span class="neg">-'+q.negMarks+'</span></div></div><div class="q-body"><div class="q-text">'+q.text+'</div><div class="options">'+o+'</div>'+s+'</div></div>'});
var pal=document.getElementById('palette');for(var i=0;i<totalQ;i++){var d=document.createElement('div');d.className='palette-item';d.id='p'+i;d.textContent=i+1;d.onclick=function(){document.getElementById('q'+this.id.slice(1)).scrollIntoView({behavior:'smooth'})};pal.appendChild(d)}
var tI=setInterval(function(){if(submitted){clearInterval(tI);return}timeLeft--;document.getElementById('timer').textContent='⏱ '+Math.floor(timeLeft/60)+':'+(timeLeft%60).toString().padStart(2,'0');if(timeLeft<=0){clearInterval(tI);submitTest()}},1000);
function selectOption(q,o){if(submitted)return;var c=document.getElementById('q'+q);c.querySelectorAll('.option').forEach(function(x){x.classList.remove('selected')});c.querySelector('.option[data-option="'+o+'"]').classList.add('selected');userAnswers[q]=o;document.getElementById('p'+q).classList.add('answered');document.getElementById('submitBtn').classList.add('visible')}
function submitTest(){if(submitted)return;submitted=true;clearInterval(tI);var sc=0,co=0,at=0;for(var i=0;i<totalQ;i++){var q=document.getElementById('q'+i),c=parseInt(q.dataset.correct),u=userAnswers[i];q.querySelectorAll('.option').forEach(function(o){var n=parseInt(o.dataset.option);o.onclick=null;if(n===c){o.classList.add('correct');o.querySelector('.option-check').textContent='✓'}else if(n===u){o.classList.add('wrong');o.querySelector('.option-check').textContent='✗'}});if(u){at++;if(u===c){sc+=2;co++}else{sc-=0.5}}var s=document.getElementById('sol'+i);if(s)s.classList.add('visible')}document.getElementById('scoreValue').textContent=co+'/'+totalQ;document.getElementById('scoreLabel').textContent='Score: '+sc+' | Correct: '+co+' | Wrong: '+(at-co);document.getElementById('scoreBanner').classList.add('visible');if(ANALYSIS&&ANALYSIS.rank){document.getElementById('analysisGrid').innerHTML='<div class="stat"><div class="value rank">'+ANALYSIS.rank+'</div><div class="label">Rank</div></div><div class="stat"><div class="value pct">'+(ANALYSIS.percentile?ANALYSIS.percentile.toFixed(2)+'%':'N/A')+'</div><div class="label">Percentile</div></div><div class="stat"><div class="value avg">'+(ANALYSIS.avgMarks?ANALYSIS.avgMarks.toFixed(2):'N/A')+'</div><div class="label">Avg</div></div><div class="stat"><div class="value total">'+(ANALYSIS.totalStudents||'N/A')+'</div><div class="label">Students</div></div>';document.getElementById('analysisCard').classList.add('visible')}document.getElementById('submitBtn').style.display='none';document.getElementById('vsBtn').style.display='inline-block';if(window.MathJax&&window.MathJax.typesetPromise)window.MathJax.typesetPromise();document.getElementById('scoreBanner').scrollIntoView({behavior:'smooth'})}
function toggleSolutions(){solsVisible=!solsVisible;document.querySelectorAll('.solution').forEach(function(s){if(solsVisible)s.classList.add('visible');else s.classList.remove('visible')});document.getElementById('vsBtn').textContent=solsVisible?'📖 Hide Solutions':'📖 Show Solutions'}
setTimeout(function(){if(window.MathJax&&window.MathJax.typesetPromise)window.MathJax.typesetPromise()},2000);
</script></body></html>'''

# ─── Folder structure ────────────────────────────────────────────────
def get_folder_path(series_name, test_title, section, subsection):
    """Build folder path: output/exam_name/category/test_title.html"""
    # Extract year from title (e.g., "SSC CGL 2025" → "2025")
    year_match = re.search(r'(20\d{2})', test_title)
    year = year_match.group(1) if year_match else "General"

    # Category from section/subsection
    category = subsection or section or "Tests"
    # Clean category name
    category = re.sub(r'[^\w\s-]', '', category).strip() or "Tests"

    # Clean test title for filename
    safe_title = re.sub(r'[^\w\s-]', '', test_title).strip()
    safe_title = re.sub(r'\s+', '_', safe_title)[:80]

    folder = OUTPUT_DIR / series_name / year / category
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe_title}.html"

# ─── Scraper ──────────────────────────────────────────────────────────
async def fetch_series_tests(context, series, cookie_str):
    """Fetch all test IDs for a series."""
    slug = series["slug"]
    platform = series["platform"]
    api_prefix = "/api/v2" if platform == "gd" else "/api/v1"
    tests = []

    try:
        url = f"{API_BASE}{api_prefix}/test-series/{slug}"
        if platform != "gd":
            url += f"?variant={platform}"
        resp = await context.request.get(url, headers={"Accept": "application/json", "Cookie": cookie_str})
        if resp.status != 200:
            print(f"  ✗ {series['name']}: HTTP {resp.status}")
            return []
        data = json.loads(await resp.text())
        details = data.get("data", {}).get("details", {})
        if not details.get("id"):
            return []

        for sec in details.get("sections", []):
            for sub in sec.get("subsections", []):
                turl = f"{API_BASE}{api_prefix}/test-series/{details['id']}/sections/{sec['id']}/tests?limit=500&subSectionId={sub['id']}"
                if platform != "gd":
                    turl += f"&variant={platform}"
                r = await context.request.get(turl, headers={"Accept": "application/json", "Cookie": cookie_str})
                if r.status == 200:
                    for t in json.loads(await r.text()).get("data", []):
                        t["_section"] = sec.get("name", "")
                        t["_subsection"] = sub.get("name", "")
                        tests.append(t)
            await asyncio.sleep(0.3)
    except Exception as e:
        print(f"  ✗ {series['name']}: {e}")
    return tests

async def scrape_test(context, test, series, cookie_str, sem):
    """Scrape one test: questions + answers + solutions + analysis → HTML."""
    async with sem:
        test_id = test["id"]
        title = test.get("title", test_id)
        slug = series["slug"]
        platform = series["platform"]
        base_url = f"https://repeatermock.com/{platform}/test-series/{slug}/test/{test_id}"

        for attempt in range(MAX_RETRIES):
            try:
                # 1. Questions
                resp = await context.request.get(f"{base_url}/attempt", headers={"Accept": "text/html", "Cookie": cookie_str})
                html = await resp.text()
                if resp.status != 200 or len(html) < 5000:
                    raise Exception(f"/attempt HTTP {resp.status}")
                payload = extract_flight_payload(html)
                questions = [clean_question(q) for q in parse_questions(payload)]
                if not questions:
                    raise Exception("No questions")

                # 2. Start + submit
                api_prefix = "/api/v2" if platform == "gd" else "/api/v1"
                resp = await context.request.post(f"{API_BASE}{api_prefix}/attempts/{test_id}/start",
                    headers={"Accept":"application/json","Content-Type":"application/json","Cookie":cookie_str}, data="{}")
                if resp.status == 429:
                    raise Exception("rate_limited")
                if resp.status != 200:
                    raise Exception(f"/start HTTP {resp.status}")

                resp = await context.request.post(f"{API_BASE}{api_prefix}/attempts/{test_id}/submit",
                    headers={"Accept":"application/json","Content-Type":"application/json","Cookie":cookie_str},
                    data=json.dumps({"answers":[],"timeTaken":1,"language":"en","interface":"classic"}))
                if resp.status == 429:
                    raise Exception("rate_limited")
                await asyncio.sleep(1)

                # 3. Solution
                resp = await context.request.get(f"{base_url}/solution", headers={"Accept":"text/html","Cookie":cookie_str})
                sol_html = await resp.text()
                sol_payload = extract_flight_payload(sol_html)
                answers = extract_json_object(sol_payload, "answersData")
                ref_table = build_rsc_ref_table(sol_payload)

                # 4. Analysis
                resp = await context.request.get(f"{base_url}/analysis", headers={"Accept":"text/html","Cookie":cookie_str})
                ana_payload = extract_flight_payload(await resp.text())
                analysis = extract_json_object(ana_payload, "analysisData")

                # 5. Generate HTML
                html_content = generate_html(questions, answers, analysis, ref_table, title)

                # 6. Save to folder
                out_path = get_folder_path(series["name"], title, test.get("_section",""), test.get("_subsection",""))
                out_path.write_text(html_content, encoding="utf-8")

                sol_count = sum(1 for q in questions if answers and resolve_solution(answers.get(q["id"],{}), ref_table))
                return {"id": test_id, "title": title, "questions": len(questions), "solutions": sol_count, "path": str(out_path)}

            except Exception as e:
                if "rate_limited" in str(e):
                    wait = min(60 * (attempt + 1), RATE_LIMIT_CAP)
                    print(f"    ⏸ Rate limited — waiting {wait}s (attempt {attempt+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait)
                    continue
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(5)
                    continue
                return {"id": test_id, "title": title, "error": str(e)}

        return {"id": test_id, "title": title, "error": "max retries"}

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-tests", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = parser.parse_args()

    progress = load_progress()
    scraped_set = set(progress["scraped"])
    failed_set = set(progress["failed"])

    print(f"\n{'='*60}")
    print(f"RepeaterMock Mass Scraper — Guest Mode (No Login)")
    print(f"{'='*60}")
    print(f"  Already scraped: {len(scraped_set)}")
    print(f"  Failed: {len(failed_set)}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Start: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        await context.add_init_script("window.console.clear=function(){};Object.defineProperty(window,'close',{value:function(){},writable:true});")
        page = await context.new_page()

        # Get guest cookies
        print("\n→ Getting guest cookies...")
        await page.goto("https://repeatermock.com/", timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        cookies = await context.cookies()
        cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies if "repeatermock" in c.get("domain",""))
        print(f"  ✓ Guest cookies: {[c['name'] for c in cookies if 'repeatermock' in c.get('domain','')]}")

        # Fetch test lists
        all_tests = []
        for series in SERIES:
            cache_key = series["slug"]
            if cache_key in progress.get("series_cache", {}):
                cached = progress["series_cache"][cache_key]
                all_tests.extend([(t, series) for t in cached])
                print(f"  ✓ {series['name']}: {len(cached)} tests (cached)")
                continue

            print(f"  Fetching: {series['name']}...")
            tests = await fetch_series_tests(context, series, cookie_str)
            progress.setdefault("series_cache", {})[cache_key] = tests
            save_progress(progress)
            all_tests.extend([(t, series) for t in tests])
            print(f"  ✓ {series['name']}: {len(tests)} tests")

        # Filter to pending
        pending = [(t, s) for t, s in all_tests if t["id"] not in scraped_set]
        print(f"\n  Total tests: {len(all_tests)} | Pending: {len(pending)}")

        if not pending:
            print("\n✓ All tests already scraped!")
            await browser.close()
            return

        if args.max_tests > 0:
            pending = pending[:args.max_tests]
            print(f"  Limited to: {len(pending)}")

        # Scrape with concurrency
        sem = asyncio.Semaphore(args.concurrency)
        done = 0
        total = len(pending)
        start_time = time.time()

        # Process in batches
        batch_size = args.concurrency * 2
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i+batch_size]
            tasks = [scrape_test(context, t, s, cookie_str, sem) for t, s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    print(f"  ✗ Error: {result}")
                    continue
                done += 1
                if "error" in result:
                    failed_set.add(result["id"])
                    progress["failed"] = list(failed_set)
                    print(f"  [{done}/{total}] ✗ {result.get('title','?')[:40]} — {result['error']}")
                else:
                    scraped_set.add(result["id"])
                    failed_set.discard(result["id"])
                    progress["scraped"] = list(scraped_set)
                    elapsed = time.time() - start_time
                    speed = done / max(elapsed, 1) * 60
                    remaining = (total - done) / max(speed / 60, 0.01) / 60
                    print(f"  [{done}/{total}] ✓ {result.get('title','?')[:40]} — Q={result.get('questions',0)} S={result.get('solutions',0)} ({speed:.0f}/min, ~{remaining:.0f}min left)")

                # Save progress every 5 tests
                if done % 5 == 0:
                    save_progress(progress)

            save_progress(progress)

        # Summary
        elapsed = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"SCRAPING COMPLETE")
        print(f"{'='*60}")
        print(f"  Total scraped: {len(scraped_set)}")
        print(f"  Failed: {len(failed_set)}")
        print(f"  Time: {elapsed/60:.1f} min")
        print(f"  Output: {OUTPUT_DIR}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
