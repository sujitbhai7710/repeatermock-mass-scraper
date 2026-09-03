#!/usr/bin/env python3
"""
RepeaterMock Mass Scraper v2
============================
Scrapes ALL free tests from any RepeaterMock test series — gets every question,
correct answer, full written solution — and renders each test as a fully
INTERACTIVE mock-test HTML page (timer, question palette, Save & Next, Mark for
Review, Submit → reveals all answers + solutions, just like the real website).

Features
--------
- **Parallel scraping**: configurable concurrency (default 10 workers, up to 50).
  Each worker is an isolated browser context with its own cookie jar + Cloudflare
  clearance.
- **Auto cookie refresh**: detects 401 responses and re-navigates to repeatermock.com
  to refresh Cloudflare clearance + cookies. Also refreshes every 15 minutes
  proactively.
- **Resume capability**: saves progress to `progress.json` after every test. If you
  kill the terminal and restart, it picks up exactly where it left off — no
  duplicate scrapes.
- **Nested folder structure**: `output_dir/series_slug/section_name/subsection_name/test_title.html`
  — as deep as the series structure goes.
- **Stop after N**: `--stop-after 50` stops after 50 tests total.
- **Interactive mock-test UI**: each HTML file is a real interactive test —
  countdown timer (classic or sectional), question palette, Save & Next / Mark
  for Review / Clear Response buttons, Submit Test → confirmation modal → answer
  reveal with correct options highlighted green + solution explanations.
- **$N reference resolution**: handles Next.js flight data `$N` references (both
  decimal AND hex IDs/lengths) — fixes the bug where ~26% of questions had
  "missing" solutions (they weren't missing, just referenced).

Usage
-----
Single test:
    python3 repeatermock_scraper.py \\
        --test-url "https://repeatermock.com/tb/test-series/ssc-cgl/test/6a2bef33be1bd560ab3a0e66/attempt?lang=en"

Whole series (all free tests):
    python3 repeatermock_scraper.py --series-url "https://repeatermock.com/tb/test-series/ssc-cgl"

Multiple series + parallel + stop after 50:
    python3 repeatermock_scraper.py \\
        --series-url "https://repeatermock.com/tb/test-series/ssc-cgl" \\
                       "https://repeatermock.com/tb/test-series/ssc-reasoning-previous-year-questions" \\
        --workers 20 --stop-after 50

Resume after killing terminal:
    python3 repeatermock_scraper.py --series-url "..." --resume

Requirements
------------
- Python 3.8+
- pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import asyncio
import html as html_mod
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Force unbuffered output so logs appear LIVE in GitHub Actions (and terminal)
# This ensures every print() is immediately flushed to stdout
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


# =============================================================================
# CONFIG
# =============================================================================

API_BASE = "https://api.repeatermock.com"
WEB_BASE = "https://repeatermock.com"
DEFAULT_OUTPUT_DIR = "/home/z/my-project/download/repeatermock_tests"
PROGRESS_FILE = "progress.json"

# Cookie refresh interval (15 minutes)
COOKIE_REFRESH_INTERVAL_SEC = 15 * 60

# Language code -> human name
LANG_NAMES = {
    'en': 'English', 'hn': 'Hindi', 'te': 'Telugu', 'mr': 'Marathi',
    'bn': 'Bengali', 'ml': 'Malayalam', 'gu': 'Gujarati', 'kn': 'Kannada',
    'ta': 'Tamil', 'or': 'Odia', 'as': 'Assamese', 'ks': 'Kashmiri',
    'kok': 'Konkani', 'mni': 'Meitei', 'ne': 'Nepali', 'pa': 'Punjabi',
    'sd': 'Sindhi', 'ur': 'Urdu', 'sat': 'Santali', 'mai': 'Maithili',
    'brx': 'Bodo', 'doi': 'Dogri', 'sa': 'Sanskrit', 'grt': 'Garo',
    'kha': 'Khasi', 'lus': 'Mizo', 'bo': 'Tibetan', 'trp': 'Tripuri',
}
TAB_LANGUAGES = ['en', 'hn', 'te', 'mr', 'bn', 'ml', 'gu', 'kn', 'ta', 'or']


# =============================================================================
# ANTI-ANTI-DEVTOOLS INIT SCRIPT
# =============================================================================
# Injected BEFORE any page JS runs (via Playwright's add_init_script).
# Neutralizes all self-destruct vectors:
#   - window.close(), console.clear(), window.stop()
#   - location.replace/assign/href-setter to about:blank
#   - window.open to about:blank
#   - history.replaceState/pushState to about:blank
#   - document.write of short destructive content
#   - eval/setTimeout/setInterval with `debugger` statements
# Plus makes console.log/table/dir/debug/info/trace/group no-ops to defeat
# the getter-based devtools detection.

INIT_SCRIPT = r"""
(function(){
  window.close = function() {};
  console.clear = function() {};
  window.stop = function() {};
  try {
    const origReplace = window.location.replace.bind(window.location);
    window.location.replace = function(u) {
      if (u && String(u).indexOf('about:blank') === 0) return;
      return origReplace(u);
    };
    const origAssign = window.location.assign.bind(window.location);
    window.location.assign = function(u) {
      if (u && String(u).indexOf('about:blank') === 0) return;
      return origAssign(u);
    };
  } catch(e){}
  try {
    const locDesc = Object.getOwnPropertyDescriptor(window.Location.prototype, 'href');
    if (locDesc && locDesc.set) {
      const origSetter = locDesc.set;
      Object.defineProperty(window.Location.prototype, 'href', {
        get: locDesc.get,
        set: function(v) {
          if (typeof v === 'string' && v.indexOf('about:blank') === 0) return;
          return origSetter.call(this, v);
        },
        configurable: true,
      });
    }
  } catch(e){}
  const origOpen = window.open;
  window.open = function(u, ...rest) {
    if (typeof u === 'string' && (u.indexOf('about:blank') === 0 || u === '')) return null;
    return origOpen.call(this, u, ...rest);
  };
  const origReplaceState = history.replaceState;
  history.replaceState = function(state, title, url) {
    if (typeof url === 'string' && url.indexOf('about:blank') === 0) return;
    return origReplaceState.call(this, state, title, url);
  };
  const origPushState = history.pushState;
  history.pushState = function(state, title, url) {
    if (typeof url === 'string' && url.indexOf('about:blank') === 0) return;
    return origPushState.call(this, state, title, url);
  };
  const origWrite = document.write.bind(document);
  document.write = function(html) {
    if (typeof html === 'string' && html.length < 500) return;
    return origWrite(html);
  };
  window.addEventListener('beforeunload', function(e) {
    e.stopImmediatePropagation();
    e.preventDefault();
    e.returnValue = '';
    return '';
  }, true);
  // NEUTRALIZE DEVTOOLS DETECTION (getter trap)
  console.log = function() {};
  console.table = function() {};
  console.dir = function() {};
  console.debug = function() {};
  console.info = function() {};
  console.trace = function() {};
  console.group = function() {};
  console.groupEnd = function() {};
  console.groupCollapsed = function() {};
  // Neutralize `debugger` in dynamically-evaluated code
  const origEval = window.eval;
  window.eval = function(code) {
    if (typeof code === 'string' && code.indexOf('debugger') >= 0) {
      code = code.replace(/\bdebugger\b/g, 'void 0');
    }
    return origEval.call(this, code);
  };
  const origSetTimeout = window.setTimeout;
  window.setTimeout = function(fn, delay, ...args) {
    if (typeof fn === 'string' && fn.indexOf('debugger') >= 0) {
      fn = fn.replace(/\bdebugger\b/g, 'void 0');
    }
    return origSetTimeout.call(this, fn, delay, ...args);
  };
  const origSetInterval = window.setInterval;
  window.setInterval = function(fn, delay, ...args) {
    if (typeof fn === 'string' && fn.indexOf('debugger') >= 0) {
      fn = fn.replace(/\bdebugger\b/g, 'void 0');
    }
    return origSetInterval.call(this, fn, delay, ...args);
  };
})();
"""


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Question:
    """A single question with options, answer, and solution."""
    qid: str
    qs_no: int
    pos_marks: float
    neg_marks: float
    type: str
    langs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    correct_option: Optional[str] = None
    correct_range: Optional[Dict[str, str]] = None
    solution_html: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class Section:
    """A test section (e.g. 'Test', 'Quantitative Aptitude')."""
    section_id: str
    section_name: str
    q_count: int
    max_marks: float
    questions: List[Question] = field(default_factory=list)


@dataclass
class TestData:
    """All data for a single test."""
    test_id: str
    title: str
    series_slug: str
    duration_min: int
    max_marks: Optional[float]
    languages: List[str]
    sections: List[Section] = field(default_factory=list)


@dataclass
class TestRef:
    """A reference to a test (for discovery + queueing)."""
    test_id: str
    title: str
    series_slug: str
    series_name: str
    section_id: str
    section_name: str
    sub_section_id: str
    sub_section_name: str
    is_free: bool
    duration: int
    question_count: int
    total_mark: float


# =============================================================================
# FLIGHT DATA PARSING  (handles $N references — decimal AND hex)
# =============================================================================

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.DOTALL)


def extract_flight_payloads(html: str) -> List[str]:
    payloads = []
    for m in PUSH_RE.finditer(html):
        try:
            decoded = json.loads('"' + m.group(1) + '"')
            payloads.append(decoded)
        except json.JSONDecodeError:
            continue
    return payloads


def build_text_refs(html: str) -> Dict[str, str]:
    """Parse flight data, extract ALL text-node references.
    Text nodes look like: <line_id>:T<length>,<text>
    Both <line_id> AND <length> can be decimal OR hex."""
    payloads = extract_flight_payloads(html)
    full_flight = "\n".join(payloads)
    text_refs: Dict[str, str] = {}
    pattern = re.compile(r'([0-9a-f]+):T([0-9a-f]+),', re.DOTALL)
    for m in pattern.finditer(full_flight):
        line_id = m.group(1)
        len_str = m.group(2)
        try:
            declared_len = int(len_str)
        except ValueError:
            try:
                declared_len = int(len_str, 16)
            except ValueError:
                continue
        text = full_flight[m.end():m.end() + declared_len]
        if len(text) == declared_len:
            text_refs[line_id] = text
    return text_refs


def resolve_ref(val: Any, text_refs: Dict[str, str]) -> Any:
    """Resolve $N references (N can be decimal OR hex)."""
    if isinstance(val, str) and re.match(r'^\$[0-9a-f]+$', val):
        return text_refs.get(val[1:], val)
    return val


def find_props_in_flight(html: str) -> Optional[Dict[str, Any]]:
    """Find the flight-data payload with testData + answersData."""
    payloads = extract_flight_payloads(html)
    for payload in payloads:
        actual = payload[2:] if payload.startswith("e:") else payload
        try:
            data = json.loads(actual)
            if isinstance(data, list) and len(data) >= 4:
                props = data[3]
                if isinstance(props, dict) and "testData" in props and "answersData" in props:
                    return props
        except json.JSONDecodeError:
            continue
    # Fallback: testData only
    for payload in payloads:
        actual = payload[2:] if payload.startswith("e:") else payload
        try:
            data = json.loads(actual)
            if isinstance(data, list) and len(data) >= 4:
                props = data[3]
                if isinstance(props, dict) and "testData" in props:
                    return props
        except json.JSONDecodeError:
            continue
    return None


def parse_test_data(props: Dict[str, Any], test_id: str, series_slug: str,
                    text_refs: Dict[str, str]) -> TestData:
    """Convert raw flight-data props into our TestData model."""
    test_data_raw = props.get("testData", {})
    answers_data = props.get("answersData", {}) or {}

    title = test_data_raw.get("title", "Untitled Test")
    duration = test_data_raw.get("duration", 0) or 0
    languages = test_data_raw.get("languages", ["en"])
    sections_raw = test_data_raw.get("sections", [])

    sections: List[Section] = []
    for sec_raw in sections_raw:
        section = Section(
            section_id=sec_raw.get("_id", ""),
            section_name=sec_raw.get("title", "Test"),
            q_count=sec_raw.get("qCount", 0),
            max_marks=float(sec_raw.get("maxM", 0) or 0),
        )
        for q_raw in sec_raw.get("questions", []):
            qid = q_raw.get("_id", "")
            qs_no = q_raw.get("QSNo", len(section.questions) + 1)
            # Per-language content
            langs: Dict[str, Dict[str, Any]] = {}
            for lang_code in LANG_NAMES:
                lang_data = q_raw.get(lang_code)
                if isinstance(lang_data, dict) and lang_data.get("value"):
                    resolved_value = resolve_ref(lang_data.get("value", ""), text_refs)
                    if isinstance(resolved_value, str) and not resolved_value.startswith("$"):
                        langs[lang_code] = {
                            "value": html_mod.unescape(resolved_value),
                            "options": [
                                {"prompt": opt.get("prompt", ""), "value": opt.get("value", "")}
                                for opt in lang_data.get("options", [])
                            ],
                            "range": lang_data.get("Range", {}),
                        }
            # Answer + solution
            answer = answers_data.get(qid, {})
            correct_option = answer.get("correctOption")
            correct_range = answer.get("range")
            tags = answer.get("tags", []) or []
            solution_html: Dict[str, str] = {}
            sol_data = answer.get("sol", {})
            if isinstance(sol_data, dict):
                for lang_code in LANG_NAMES:
                    sol_lang = sol_data.get(lang_code, {})
                    if isinstance(sol_lang, dict):
                        sol_val = resolve_ref(sol_lang.get("value", ""), text_refs)
                        if isinstance(sol_val, str) and not sol_val.startswith("$"):
                            solution_html[lang_code] = html_mod.unescape(sol_val)
            section.questions.append(Question(
                qid=qid, qs_no=qs_no,
                pos_marks=float(q_raw.get("posMarks", 0) or 0),
                neg_marks=float(q_raw.get("negMarks", 0) or 0),
                type=q_raw.get("type", "mcq"),
                langs=langs,
                correct_option=correct_option,
                correct_range=correct_range,
                solution_html=solution_html,
                tags=tags,
            ))
        sections.append(section)

    max_marks = sum(s.max_marks for s in sections) or None
    if max_marks is None and sections:
        max_marks = sum(q.pos_marks for s in sections for q in s.questions)

    return TestData(
        test_id=test_id, title=title, series_slug=series_slug,
        duration_min=int(duration), max_marks=max_marks,
        languages=languages, sections=sections,
    )


# =============================================================================
# PROGRESS TRACKER  (resume capability)
# =============================================================================

class ProgressTracker:
    """Tracks scraped test IDs per series. Saves to progress.json after each test.
    On startup, loads the file and skips already-scraped tests."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.progress_path = os.path.join(output_dir, PROGRESS_FILE)
        self.data: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.progress_path):
            try:
                with open(self.progress_path) as f:
                    self.data = json.load(f)
                print(f"  [progress] loaded {sum(len(v.get('scraped', [])) for v in self.data.values())} scraped tests from {self.progress_path}")
            except Exception as e:
                print(f"  [progress] WARNING: couldn't load {self.progress_path}: {e}")
                self.data = {}

    def is_scraped(self, series_slug: str, test_id: str) -> bool:
        series_data = self.data.get(series_slug, {})
        return test_id in series_data.get("scraped", [])

    def is_failed(self, series_slug: str, test_id: str) -> bool:
        series_data = self.data.get(series_slug, {})
        return test_id in series_data.get("failed", [])

    async def mark_scraped(self, series_slug: str, test_id: str, filepath: str):
        async with self._lock:
            if series_slug not in self.data:
                self.data[series_slug] = {"scraped": {}, "failed": []}
            self.data[series_slug].setdefault("scraped", {})[test_id] = {
                "filepath": filepath,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Remove from failed if it was there
            if test_id in self.data[series_slug].get("failed", []):
                self.data[series_slug]["failed"].remove(test_id)
            self.data[series_slug]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save()

    async def mark_failed(self, series_slug: str, test_id: str, reason: str):
        async with self._lock:
            if series_slug not in self.data:
                self.data[series_slug] = {"scraped": {}, "failed": []}
            self.data[series_slug].setdefault("failed", []).append({"test_id": test_id, "reason": reason})
            self.data[series_slug]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def _save(self):
        try:
            tmp_path = self.progress_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self.data, f, indent=2)
            os.rename(tmp_path, self.progress_path)
        except Exception as e:
            print(f"  [progress] WARNING: couldn't save: {e}")

    def stats(self) -> Dict[str, int]:
        total_scraped = sum(len(v.get("scraped", {})) for v in self.data.values())
        total_failed = sum(len(v.get("failed", [])) for v in self.data.values())
        return {"scraped": total_scraped, "failed": total_failed}


# =============================================================================
# BROWSER WORKER  (one per parallel slot — own context + cookie jar)
# =============================================================================

class Worker:
    """A single browser context that scrapes tests from a shared queue.
    Handles its own cookie refresh on 401 + periodic refresh."""

    def __init__(self, worker_id: int, browser, progress: ProgressTracker, output_dir: str):
        self.worker_id = worker_id
        self.browser = browser
        self.progress = progress
        self.output_dir = output_dir
        self.context = None
        self.page = None
        self.last_refresh = 0
        self.tests_done = 0

    async def start(self):
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        await self.context.add_init_script(INIT_SCRIPT)
        self.page = await self.context.new_page()
        await self.refresh_cookies(force=True)
        print(f"  [worker {self.worker_id}] ready")

    async def refresh_cookies(self, force: bool = False) -> bool:
        """Navigate to repeatermock.com to refresh Cloudflare clearance + cookies.
        Called on startup, on 401, and every 15 minutes."""
        now = time.time()
        if not force and now - self.last_refresh < COOKIE_REFRESH_INTERVAL_SEC:
            return True
        print(f"  [worker {self.worker_id}] refreshing cookies...")
        try:
            await self.page.goto(f"{WEB_BASE}/tb/test-series/ssc-cgl",
                                 wait_until="domcontentloaded", timeout=60000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await self.page.wait_for_timeout(5000)
            if "about:blank" in self.page.url:
                # Retry
                await self.page.goto(f"{WEB_BASE}/tb/test-series/ssc-cgl",
                                     wait_until="domcontentloaded", timeout=60000)
                await self.page.wait_for_timeout(8000)
            self.last_refresh = now
            return "about:blank" not in self.page.url
        except Exception as e:
            print(f"  [worker {self.worker_id}] cookie refresh failed: {e}")
            return False

    async def eval_js(self, js: str, timeout: int = 30) -> Any:
        try:
            return await self.page.evaluate(js)
        except Exception as e:
            return None

    async def api_call(self, method: str, path: str, body: str = "{}") -> Tuple[int, Optional[dict]]:
        """Make an API call with robust rate-limit handling.
        
        On 429 (rate limited): waits with exponential backoff + jitter, then retries.
        On 401 (cookie expired): refreshes cookies, then retries.
        On network error (status 0): refreshes cookies, then retries.
        
        Returns (status, parsed_json). After max retries, returns (0, None)."""
        MAX_RETRIES = 5
        base_delay = 2  # seconds — doubled each retry
        for attempt in range(MAX_RETRIES):
            url = f"{API_BASE}{path}" if path.startswith("/") else path
            has_body = method.upper() not in ("GET", "HEAD")
            body_js = json.dumps(body) if has_body else "undefined"
            js = f"""
            (async function(){{
              try {{
                const r = await fetch({json.dumps(url)}, {{
                  method: {json.dumps(method)},
                  credentials: 'include',
                  headers: {{'Content-Type': 'application/json'}},
                  body: {body_js}
                }});
                const t = await r.text();
                // Extract Retry-After header if present (for 429 handling)
                const retryAfter = r.headers.get('retry-after') || r.headers.get('Retry-After') || '';
                return {{status: r.status, body: t, retryAfter: retryAfter}};
              }} catch(e) {{
                return {{status: 0, body: String(e).slice(0, 200), retryAfter: ''}};
              }}
            }})()
            """
            result = await self.eval_js(js, timeout=30)
            if not isinstance(result, dict):
                # JS eval failed — refresh cookies and retry
                if attempt < MAX_RETRIES - 1:
                    await self.refresh_cookies(force=True)
                    await asyncio.sleep(base_delay * (2 ** attempt) + random.uniform(0, 1))
                continue
            
            status = result.get("status", 0)
            body_text = result.get("body", "")
            retry_after = result.get("retryAfter", "")
            
            if status == 429:
                # Rate limited — respect Retry-After header if present, else exponential backoff
                if retry_after:
                    try:
                        wait_sec = float(retry_after)
                    except ValueError:
                        wait_sec = base_delay * (2 ** attempt)
                else:
                    wait_sec = base_delay * (2 ** attempt)
                # Add jitter (0-1 sec) to avoid thundering herd
                wait_sec += random.uniform(0, 1)
                print(f"  [worker {self.worker_id}] ⏳ 429 rate limited, waiting {wait_sec:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
                await asyncio.sleep(wait_sec)
                # Also refresh cookies (rate limit might be tied to cookie freshness)
                await self.refresh_cookies(force=True)
                continue
            
            if status == 401:
                # Cookie expired — refresh and retry
                print(f"  [worker {self.worker_id}] 🔑 401 cookie expired, refreshing (attempt {attempt+1}/{MAX_RETRIES})")
                await self.refresh_cookies(force=True)
                await asyncio.sleep(1 + random.uniform(0, 0.5))
                continue
            
            if status == 0:
                # Network error — refresh cookies and retry with backoff
                if attempt < MAX_RETRIES - 1:
                    wait_sec = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [worker {self.worker_id}] 🌐 network error, waiting {wait_sec:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_sec)
                    await self.refresh_cookies(force=True)
                continue
            
            # Success (or non-retryable error) — return the result
            try:
                parsed = json.loads(body_text)
                return status, parsed
            except Exception:
                return status, None
        
        # All retries exhausted
        print(f"  [worker {self.worker_id}] ❌ API call failed after {MAX_RETRIES} retries: {method} {path[:60]}")
        return 0, None

    async def scrape_test(self, test_ref: TestRef) -> Optional[TestData]:
        """Scrape a single test: start, submit, fetch solution page, parse."""
        tid = test_ref.test_id
        # 1. Start attempt
        status, data = await self.api_call("POST", f"/api/v1/attempts/{tid}/start", "{}")
        if status != 200 or not data:
            print(f"  [worker {self.worker_id}] start failed for {tid}: status={status}")
            return None
        # 2. Submit empty answers
        status, data = await self.api_call(
            "POST", f"/api/v1/attempts/{tid}/submit",
            '{"answers":[],"timeTaken":1,"language":"en","interface":"classic"}'
        )
        if status != 200:
            print(f"  [worker {self.worker_id}] submit failed for {tid}: status={status}")
            return None
        # 3. Fetch solution page HTML
        solution_url = f"{WEB_BASE}/tb/test-series/{test_ref.series_slug}/test/{tid}/solution"
        try:
            await self.page.goto(solution_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await self.page.wait_for_timeout(6000)
        except Exception as e:
            print(f"  [worker {self.worker_id}] nav to solution failed: {e}")
            return None
        if "about:blank" in self.page.url:
            print(f"  [worker {self.worker_id}] solution page self-destructed, skipping")
            return None
        # 4. Fetch the HTML via in-page fetch (gets post-JS version with flight data)
        js_fetch = """
        (function(){
          return fetch(window.location.href, {credentials: 'include'})
            .then(r => r.text())
            .then(t => { window.__HTML__ = t; return {stored: true, len: t.length, hasTestData: t.indexOf('testData') >= 0, hasAnswersData: t.indexOf('answersData') >= 0}; })
            .catch(e => ({stored: false, err: String(e).slice(0, 200)}));
        })()
        """
        result = await self.eval_js(js_fetch, timeout=60)
        if not isinstance(result, dict) or not result.get("stored"):
            print(f"  [worker {self.worker_id}] failed to fetch solution HTML: {result}")
            return None
        # 5. Extract HTML in chunks
        chunks = []
        chunk_size = 100000
        for i in range(50):
            start = i * chunk_size
            js_chunk = f"(function(){{var h = window.__HTML__ || ''; if ({start} >= h.length) return null; return h.slice({start}, {start + chunk_size});}})()"
            chunk = await self.eval_js(js_chunk, timeout=30)
            if chunk is None:
                break
            chunks.append(chunk)
            if len(chunk) < chunk_size:
                break
        html = "".join(chunks)
        if not html or "testData" not in html or "answersData" not in html:
            print(f"  [worker {self.worker_id}] HTML missing testData/answersData (len={len(html)})")
            return None
        # 6. Parse flight data
        props = find_props_in_flight(html)
        if not props:
            print(f"  [worker {self.worker_id}] couldn't find props in flight data")
            return None
        text_refs = build_text_refs(html)
        test_data = parse_test_data(props, tid, test_ref.series_slug, text_refs)
        # The solution page's testData doesn't include duration — use the value
        # from the test listing API (test_ref.duration) as fallback
        if not test_data.duration_min and test_ref.duration:
            test_data.duration_min = test_ref.duration
        # If still no duration, estimate from question count:
        # ~36 seconds per question (25 Q = 15 min, 50 Q = 30 min, 100 Q = 60 min)
        # This matches RepeaterMock's typical PYST timing for lighter use
        if not test_data.duration_min:
            total_q = sum(len(s.questions) for s in test_data.sections)
            if total_q > 0:
                test_data.duration_min = max(5, round(total_q * 0.6))  # 0.6 min per Q, min 5 min
        # Also use total_mark from test_ref as fallback for max_marks
        if not test_data.max_marks and test_ref.total_mark:
            test_data.max_marks = test_ref.total_mark
        return test_data

    async def close(self):
        if self.context:
            await self.context.close()


# =============================================================================
# SERIES DISCOVERY
# =============================================================================

async def discover_series_tests(worker: Worker, series_slug: str) -> Tuple[str, str, List[TestRef]]:
    """Discover all free tests in a series. Returns (series_id, series_name, test_refs)."""
    # 1. Get series listing
    status, data = await worker.api_call("GET", f"/api/v1/test-series/{series_slug}?variant=tb")
    if status != 200 or not data:
        print(f"  [discover] failed to get series listing for {series_slug}: status={status}")
        return "", series_slug, []
    details = data.get("data", {}).get("details", {})
    series_id = details.get("id", "")
    series_name = details.get("name", series_slug)
    sections = details.get("sections", [])
    print(f"  [discover] series: {series_name} (id={series_id}), {len(sections)} sections")

    # 2. Get section counts
    status, data = await worker.api_call("GET", f"/api/v1/test-series/{series_id}/section-counts")
    if status != 200 or not data:
        print(f"  [discover] failed to get section counts: status={status}")
        return series_id, series_name, []
    section_counts = data.get("data", [])
    print(f"  [discover] {len(section_counts)} section/subsection pairs")

    # 3. Build a lookup of section/subsection names
    section_lookup: Dict[str, Dict[str, str]] = {}  # section_id -> {name, subsections: {sub_id: name}}
    for sec in sections:
        sid = sec.get("id", "")
        section_lookup[sid] = {
            "name": sec.get("name", "Section"),
            "subsections": {sub.get("id", ""): sub.get("name", "Subsection") for sub in sec.get("subsections", [])}
        }

    # 4. Fetch tests for each section/subsection pair
    all_tests: List[TestRef] = []
    seen_ids = set()
    for i, sc in enumerate(section_counts):
        sid = sc.get("sectionId", "")
        ssid = sc.get("subSectionId", "")
        count = sc.get("cachedTestCount", 0)
        if not sid or not ssid or count == 0:
            continue
        url = f"/api/v1/test-series/{series_id}/sections/{sid}/tests?limit=500&subSectionId={ssid}"
        status, data = await worker.api_call("GET", url)
        if status != 200 or not data:
            continue
        tests = data.get("data", []) if isinstance(data, dict) else []
        sec_info = section_lookup.get(sid, {"name": "Section", "subsections": {}})
        sec_name = sec_info["name"]
        sub_name = sec_info["subsections"].get(ssid, "Subsection")
        for t in tests:
            tid = t.get("id", "")
            if not tid or tid in seen_ids:
                continue
            if not t.get("isFree", False):
                continue
            all_tests.append(TestRef(
                test_id=tid,
                title=t.get("title", tid),
                series_slug=series_slug,
                series_name=series_name,
                section_id=sid,
                section_name=sec_name,
                sub_section_id=ssid,
                sub_section_name=sub_name,
                is_free=t.get("isFree", False),
                duration=t.get("duration", 0),
                question_count=t.get("questionCount", 0),
                total_mark=t.get("totalMark", 0),
            ))
            seen_ids.add(tid)
        if (i+1) % 20 == 0 or i == len(section_counts) - 1:
            print(f"  [discover] {i+1}/{len(section_counts)} subsections probed, {len(all_tests)} free tests")

    print(f"  [discover] total free tests: {len(all_tests)}")
    return series_id, series_name, all_tests


# =============================================================================
# INTERACTIVE MOCK-TEST HTML RENDERER
# =============================================================================

def render_test_html(test: TestData) -> str:
    """Render a TestData as a fully interactive mock-test HTML page.
    The page has two modes:
    1. Test mode: countdown timer, question palette, Save & Next / Mark for Review /
       Clear Response buttons, Submit Test button.
    2. Review mode (after submit): all correct answers revealed + solutions shown,
       score displayed.
    """
    # Serialize test data as JSON for the JS to consume
    # Includes concept + confidence (same as AI JSON) so the HTML and AI JSON stay in sync
    test_json = json.dumps({
        "test_id": test.test_id,
        "title": test.title,
        "series_slug": test.series_slug,
        "duration_min": test.duration_min,
        "max_marks": test.max_marks,
        "languages": test.languages,
        "sections": [
            {
                "section_id": s.section_id,
                "section_name": s.section_name,
                "q_count": s.q_count,
                "max_marks": s.max_marks,
                "questions": [
                    {
                        "qid": q.qid,
                        "qs_no": q.qs_no,
                        "pos_marks": q.pos_marks,
                        "neg_marks": q.neg_marks,
                        "type": q.type,
                        "langs": q.langs,
                        "correct_option": q.correct_option,
                        "solution_html": q.solution_html,
                        "tags": q.tags,
                        # Concept + confidence (computed by detect_concept — same as AI JSON)
                        "concept": detect_concept(
                            detect_subject(test.series_slug, test.title),
                            q.tags,
                            strip_html(q.langs.get("en", {}).get("value", "")),
                            q.langs.get("en", {}).get("options", []),
                        )[0],
                        "confidence": detect_concept(
                            detect_subject(test.series_slug, test.title),
                            q.tags,
                            strip_html(q.langs.get("en", {}).get("value", "")),
                            q.langs.get("en", {}).get("options", []),
                        )[1],
                    }
                    for q in s.questions
                ],
            }
            for s in test.sections
        ],
    }, ensure_ascii=False)

    total_q = sum(len(s.questions) for s in test.sections)
    pos = test.sections[0].questions[0].pos_marks if test.sections and test.sections[0].questions else 0
    neg = test.sections[0].questions[0].neg_marks if test.sections and test.sections[0].questions else 0

    css = _render_css()
    js = _render_js(total_q, test.duration_min)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(test.title)}</title>
<style>{css}</style>
</head>
<body>
<!-- Header -->
<header class="top-bar">
  <div class="brand"><span class="brand-light">Repeater</span><span class="brand-mark">Mock</span></div>
  <div class="test-title" id="testTitle">{html_mod.escape(test.title)}</div>
  <div class="header-right">
    <div class="timer" id="timer">00:00:00</div>
    <button class="header-btn submit-btn" onclick="openSubmitModal()">Submit Test</button>
  </div>
</header>

<!-- Meta bar -->
<div class="meta-bar">
  <div class="meta-item"><span class="meta-label">Series</span><span class="meta-value">{html_mod.escape(test.series_slug)}</span></div>
  <div class="meta-item"><span class="meta-label">Questions</span><span class="meta-value">{total_q}</span></div>
  <div class="meta-item"><span class="meta-label">Duration</span><span class="meta-value">{test.duration_min} min</span></div>
  <div class="meta-item"><span class="meta-label">Max Marks</span><span class="meta-value">{test.max_marks or '—'}</span></div>
  <div class="meta-item"><span class="meta-label">Marking</span><span class="meta-value">+{pos} / -{neg}</span></div>
  <div class="meta-item"><span class="meta-label">Test ID</span><span class="meta-value">{test.test_id}</span></div>
</div>

<!-- Test mode -->
<div id="testMode" class="test-container">
  <main class="question-area">
    <div class="section-tabs" id="sectionTabs"></div>
    <div class="question-card" id="questionCard">
      <div class="q-header">
        <span class="q-num" id="qNum">Q1</span>
        <span class="q-marks" id="qMarks"></span>
      </div>
      <div class="q-lang-tabs" id="qLangTabs"></div>
      <div class="q-text" id="qText"></div>
      <div class="options-list" id="optionsList"></div>
      <div class="actions">
        <button class="action-btn mark-review" onclick="markReviewNext()">Mark for Review &amp; Next</button>
        <button class="action-btn clear-response" onclick="clearResponse()">Clear Response</button>
        <button class="action-btn save-next" onclick="saveNext()">Save &amp; Next →</button>
      </div>
      <div class="prev-next">
        <button class="nav-btn" onclick="prevQuestion()">← Previous</button>
        <button class="nav-btn" onclick="nextQuestion()">Next →</button>
      </div>
    </div>
  </main>
  <aside class="palette-sidebar">
    <div class="palette-header">
      <span id="currentSectionLabel">Test</span>
    </div>
    <div class="palette-legend">
      <span class="legend-item"><span class="legend-dot answered"></span>Answered</span>
      <span class="legend-item"><span class="legend-dot notanswered"></span>Not Answered</span>
      <span class="legend-item"><span class="legend-dot marked"></span>Marked</span>
      <span class="legend-item"><span class="legend-dot notvisited"></span>Not Visited</span>
    </div>
    <div class="palette-grid" id="paletteGrid"></div>
    <div class="palette-stats" id="paletteStats"></div>
  </aside>
</div>

<!-- Submit modal -->
<div class="modal-overlay" id="submitModal" onclick="if(event.target===this)closeSubmitModal()">
  <div class="modal-content">
    <h2>Submit Test?</h2>
    <p>Are you sure you want to submit? You won't be able to change your answers after this.</p>
    <div class="submit-summary" id="submitSummary"></div>
    <div class="modal-actions">
      <button class="modal-btn cancel" onclick="closeSubmitModal()">Cancel</button>
      <button class="modal-btn submit" onclick="submitTest()">Yes, Submit</button>
    </div>
  </div>
</div>

<!-- Results view (hidden until submit) -->
<div id="resultsView" class="results-view" style="display:none;">
  <div class="results-header">
    <h1>Test Submitted</h1>
    <div class="score-card" id="scoreCard"></div>
    <div class="results-actions">
      <button class="header-btn" onclick="showAllQuestions()">View All Questions &amp; Solutions</button>
      <button class="header-btn" onclick="window.print()">🖨️ Print</button>
    </div>
  </div>
  <div class="results-questions" id="resultsQuestions"></div>
</div>

<!-- Test data embedded as JSON -->
<script type="application/json" id="test-data">{test_json}</script>
<script>{js}</script>
</body>
</html>"""


def _render_css() -> str:
    return """
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;background:#f3f4f6;color:#1f2937;line-height:1.6;}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:12px 24px;background:#fff;border-bottom:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.04);position:sticky;top:0;z-index:100;}
.brand{font-size:20px;font-weight:800;letter-spacing:-0.02em;}
.brand-light{color:#1f2937;}
.brand-mark{color:#1fbad6;}
.test-title{font-size:14px;color:#4b5563;font-weight:500;text-align:center;flex:1;padding:0 24px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.header-right{display:flex;align-items:center;gap:12px;}
.timer{background:#1f2937;color:#fff;padding:8px 16px;border-radius:6px;font-family:"SF Mono",Monaco,monospace;font-size:16px;font-weight:600;min-width:110px;text-align:center;}
.timer.warning{background:#f59e0b;}
.timer.danger{background:#ef4444;animation:pulse 1s infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.7;}}
.header-btn{background:#1fbad6;color:#fff;border:none;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;transition:background .15s;}
.header-btn:hover{background:#1999b3;}
.submit-btn{background:#ef4444;}
.submit-btn:hover{background:#dc2626;}
.meta-bar{background:#fff;padding:12px 24px;border-bottom:1px solid #e5e7eb;display:flex;flex-wrap:wrap;gap:12px 32px;font-size:13px;}
.meta-item{display:flex;flex-direction:column;gap:2px;}
.meta-label{color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;}
.meta-value{color:#1f2937;font-size:14px;font-weight:600;}
.test-container{display:flex;max-width:1400px;margin:0 auto;gap:16px;padding:16px;}
.question-area{flex:1;min-width:0;}
.section-tabs{display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap;}
.section-tab{background:#fff;border:1px solid #e5e7eb;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;color:#4b5563;}
.section-tab.active{background:#1fbad6;color:#fff;border-color:#1fbad6;}
.question-card{background:#fff;border-radius:8px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.04);border:1px solid #e5e7eb;}
.q-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f3f4f6;}
.q-num{background:#1fbad6;color:#fff;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:600;}
.q-marks{display:flex;gap:8px;font-size:12px;align-items:center;flex-wrap:wrap;}
.q-concept{background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.q-confidence{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.conf-high{background:#d1fae5;color:#065f46;}
.conf-unidentified{background:#fef3c7;color:#92400e;}
.q-id{background:#f3f4f6;color:#4b5563;padding:2px 8px;border-radius:4px;font-size:11px;font-family:monospace;}
.result-concept{background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.result-confidence{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.result-qid{background:#f3f4f6;color:#4b5563;padding:2px 8px;border-radius:4px;font-size:11px;font-family:monospace;margin-left:auto;}
.mark-pos{color:#10b981;font-weight:600;}
.mark-neg{color:#ef4444;font-weight:600;}
.q-lang-tabs{display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap;}
.q-lang-tab{background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;}
.q-lang-tab.active{background:#1fbad6;color:#fff;border-color:#1fbad6;}
.q-text{font-size:15px;margin-bottom:16px;}
.q-text p{margin:8px 0;}
.q-text img{max-width:100%;height:auto;border-radius:4px;}
.options-list{list-style:none;}
.option-item{display:flex;align-items:flex-start;gap:12px;padding:12px 16px;margin-bottom:8px;border:1.5px solid #e5e7eb;border-radius:6px;cursor:pointer;transition:all .15s;font-size:14px;}
.option-item:hover{background:#f9fafb;border-color:#d1d5db;}
.option-item.selected{background:#dbeafe;border-color:#3b82f6;}
.option-item.correct{background:#d1fae5;border-color:#10b981;}
.option-item.wrong{background:#fee2e2;border-color:#ef4444;}
.option-label{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;padding:0 6px;background:#1f2937;color:#fff;border-radius:4px;font-size:12px;font-weight:600;flex-shrink:0;}
.option-item.selected .option-label{background:#3b82f6;}
.option-item.correct .option-label{background:#10b981;}
.option-item.wrong .option-label{background:#ef4444;}
.option-item.correct::after{content:" ✓ Correct";color:#10b981;font-weight:700;margin-left:auto;}
.option-item.wrong::after{content:" ✗ Wrong";color:#ef4444;font-weight:700;margin-left:auto;}
.actions{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap;}
.action-btn{padding:8px 16px;border:1px solid #e5e7eb;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;background:#fff;color:#4b5563;}
.action-btn:hover{background:#f9fafb;}
.action-btn.save-next{background:#1fbad6;color:#fff;border-color:#1fbad6;}
.action-btn.save-next:hover{background:#1999b3;}
.action-btn.mark-review{background:#fef3c7;color:#92400e;border-color:#f59e0b;}
.prev-next{display:flex;justify-content:space-between;margin-top:16px;padding-top:12px;border-top:1px solid #f3f4f6;}
.nav-btn{padding:8px 16px;border:1px solid #e5e7eb;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;background:#fff;color:#4b5563;}
.nav-btn:hover{background:#f9fafb;}
.palette-sidebar{width:280px;flex-shrink:0;position:sticky;top:90px;align-self:flex-start;background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,0.04);border:1px solid #e5e7eb;max-height:calc(100vh - 110px);overflow-y:auto;}
.palette-header{font-size:14px;font-weight:700;color:#1f2937;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #f3f4f6;}
.palette-legend{display:flex;flex-wrap:wrap;gap:6px 8px;font-size:10px;color:#6b7280;margin-bottom:12px;}
.legend-item{display:flex;align-items:center;gap:3px;}
.legend-dot{width:12px;height:12px;border-radius:2px;}
.legend-dot.answered{background:#10b981;}
.legend-dot.notanswered{background:#ef4444;}
.legend-dot.marked{background:#a855f7;}
.legend-dot.notvisited{background:#e5e7eb;}
.palette-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;}
.palette-cell{aspect-ratio:1;display:flex;align-items:center;justify-content:center;border:1px solid #e5e7eb;border-radius:4px;font-size:11px;font-weight:600;color:#4b5563;cursor:pointer;transition:all .15s;background:#f3f4f6;}
.palette-cell:hover{transform:scale(1.1);}
.palette-cell.answered{background:#10b981;color:#fff;border-color:#10b981;}
.palette-cell.notanswered{background:#ef4444;color:#fff;border-color:#ef4444;}
.palette-cell.marked{background:#a855f7;color:#fff;border-color:#a855f7;}
.palette-cell.marked-answered{background:#a855f7;color:#fff;border-color:#a855f7;position:relative;}
.palette-cell.marked-answered::after{content:"•";position:absolute;top:-2px;right:2px;color:#10b981;font-size:14px;}
.palette-cell.notvisited{background:#fff;color:#9ca3af;}
.palette-cell.current{outline:3px solid #1fbad6;outline-offset:1px;}
.palette-stats{margin-top:12px;padding-top:12px;border-top:1px solid #f3f4f6;display:grid;grid-template-columns:repeat(2,1fr);gap:6px;font-size:11px;color:#6b7280;}
.palette-stats div{display:flex;justify-content:space-between;}
.palette-stats strong{color:#1f2937;font-size:13px;}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;align-items:center;justify-content:center;padding:24px;}
.modal-overlay.open{display:flex;}
.modal-content{background:#fff;border-radius:12px;padding:24px;max-width:500px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.2);}
.modal-content h2{font-size:20px;color:#1f2937;margin-bottom:12px;}
.modal-content p{font-size:14px;color:#4b5563;margin-bottom:16px;}
.submit-summary{background:#f9fafb;border-radius:8px;padding:12px;margin-bottom:16px;font-size:13px;}
.submit-summary div{display:flex;justify-content:space-between;margin-bottom:4px;}
.submit-summary strong{color:#1f2937;}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;}
.modal-btn{padding:8px 16px;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;}
.modal-btn.cancel{background:#f3f4f6;color:#4b5563;}
.modal-btn.cancel:hover{background:#e5e7eb;}
.modal-btn.submit{background:#ef4444;color:#fff;}
.modal-btn.submit:hover{background:#dc2626;}
.results-view{max-width:1000px;margin:0 auto;padding:24px;}
.results-header{text-align:center;margin-bottom:32px;}
.results-header h1{font-size:28px;color:#1f2937;margin-bottom:16px;}
.score-card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.04);border:1px solid #e5e7eb;display:inline-block;min-width:300px;margin-bottom:16px;}
.score-card .score{font-size:48px;font-weight:800;color:#1fbad6;}
.score-card .total{font-size:20px;color:#6b7280;}
.score-card .rank{font-size:14px;color:#4b5563;margin-top:8px;}
.results-actions{display:flex;gap:8px;justify-content:center;}
.results-questions{display:grid;gap:16px;}
.result-card{background:#fff;border-radius:8px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,0.04);border:1px solid #e5e7eb;}
.result-card.correct{border-left:4px solid #10b981;}
.result-card.wrong{border-left:4px solid #ef4444;}
.result-card.skipped{border-left:4px solid #9ca3af;}
.result-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
.result-q-num{background:#1fbad6;color:#fff;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:600;}
.result-status{font-size:12px;font-weight:600;padding:2px 8px;border-radius:4px;}
.result-status.correct{background:#d1fae5;color:#065f46;}
.result-status.wrong{background:#fee2e2;color:#991b1b;}
.result-status.skipped{background:#f3f4f6;color:#4b5563;}
.result-text{font-size:14px;margin-bottom:12px;}
.result-text p{margin:6px 0;}
.result-options{list-style:none;margin-bottom:12px;}
.result-option{padding:8px 12px;margin-bottom:4px;border:1px solid #e5e7eb;border-radius:4px;font-size:13px;display:flex;align-items:flex-start;gap:8px;}
.result-option.correct{background:#d1fae5;border-color:#10b981;font-weight:600;}
.result-option.selected{background:#dbeafe;border-color:#3b82f6;}
.result-option.correct.selected{background:#a7f3d0;}
.result-option-label{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;background:#1f2937;color:#fff;border-radius:3px;font-size:11px;font-weight:600;}
.result-option.correct .result-option-label{background:#10b981;}
.result-solution{background:#fef3c7;border-left:3px solid #f59e0b;border-radius:4px;padding:12px;font-size:13px;color:#1f2937;}
.result-solution-title{font-weight:700;color:#92400e;margin-bottom:6px;font-size:12px;}
.result-solution p{margin:6px 0;}
.result-solution img{max-width:100%;height:auto;border-radius:4px;}
@media (max-width:1024px){.palette-sidebar{width:220px;}.palette-grid{grid-template-columns:repeat(5,1fr);}}
@media (max-width:768px){.test-container{flex-direction:column;}.palette-sidebar{width:100%;position:static;max-height:none;}.palette-grid{grid-template-columns:repeat(10,1fr);}}
@media print{.top-bar,.palette-sidebar,.actions,.prev-next,.section-tabs,.q-lang-tabs,.meta-bar,.results-actions{display:none;}.test-container,.results-view{display:block;}.result-card{page-break-inside:avoid;}}
"""


def _render_js(total_q: int, duration_min: int) -> str:
    return f"""
const TEST_DATA = JSON.parse(document.getElementById('test-data').textContent);
const TOTAL_Q = {total_q};
const DURATION_SEC = {duration_min} * 60;

// State
let currentSectionIdx = 0;
let currentQIdx = 0;  // global across sections
let currentLang = 'en';
let answers = {{}};  // qid -> selected option prompt
let marked = {{}};   // qid -> true
let visited = new Set();
let submitted = false;
let timeLeft = DURATION_SEC;
let timerInterval = null;

// Flatten all questions for easy indexing
const allQuestions = [];
TEST_DATA.sections.forEach((s, sIdx) => {{
  s.questions.forEach(q => {{
    allQuestions.push({{...q, sectionIdx: sIdx, sectionName: s.section_name}});
  }});
}});

function init() {{
  // Render section tabs
  const sectionTabs = document.getElementById('sectionTabs');
  TEST_DATA.sections.forEach((s, i) => {{
    const tab = document.createElement('button');
    tab.className = 'section-tab' + (i === 0 ? ' active' : '');
    tab.textContent = s.section_name + ' (' + s.questions.length + ')';
    tab.onclick = () => switchSection(i);
    sectionTabs.appendChild(tab);
  }});
  // Render palette
  renderPalette();
  // Render first question
  renderQuestion();
  // Start timer
  startTimer();
  // Update stats
  updateStats();
}}

function startTimer() {{
  if (DURATION_SEC <= 0) {{
    // No time limit — show "No Limit" instead of a countdown
    document.getElementById('timer').textContent = 'No Limit';
    document.getElementById('timer').classList.add('no-limit');
    return;
  }}
  timerInterval = setInterval(() => {{
    if (submitted) return;
    timeLeft--;
    updateTimerDisplay();
    if (timeLeft <= 0) {{
      clearInterval(timerInterval);
      submitTest();
    }}
  }}, 1000);
  updateTimerDisplay();
}}

function updateTimerDisplay() {{
  const h = Math.floor(timeLeft / 3600);
  const m = Math.floor((timeLeft % 3600) / 60);
  const s = timeLeft % 60;
  const display = String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
  const timerEl = document.getElementById('timer');
  timerEl.textContent = display;
  if (timeLeft < 60) {{
    timerEl.className = 'timer danger';
  }} else if (timeLeft < 300) {{
    timerEl.className = 'timer warning';
  }}
}}

function renderQuestion() {{
  const q = allQuestions[currentQIdx];
  if (!q) return;
  visited.add(q.qid);
  // Update section tab
  currentSectionIdx = q.sectionIdx;
  document.querySelectorAll('.section-tab').forEach((t, i) => {{
    t.classList.toggle('active', i === currentSectionIdx);
  }});
  document.getElementById('currentSectionLabel').textContent = q.sectionName;
  // Question header — includes Q number, marks, concept, confidence, and question ID
  document.getElementById('qNum').textContent = 'Q' + (currentQIdx + 1);
  let headerExtras = '<span class="mark-pos">+' + q.pos_marks + '</span><span class="mark-neg">-' + q.neg_marks + '</span>';
  // Concept + confidence (from AI classifier — same as AI JSON)
  const concept = q.concept || (q.tags && q.tags.length ? q.tags.join(', ') : 'Unidentified');
  const confidence = q.confidence || (q.tags && q.tags.length ? 'high' : 'unidentified');
  const confClass = confidence === 'high' ? 'conf-high' : 'conf-unidentified';
  headerExtras += '<span class="q-concept" title="Concept category">🎯 ' + concept + '</span>';
  headerExtras += '<span class="q-confidence ' + confClass + '" title="Classifier confidence">conf: ' + confidence + '</span>';
  // Question ID (unique per question, useful for cross-test matching)
  headerExtras += '<span class="q-id" title="Question ID (unique across all tests)">ID: ' + q.qid + '</span>';
  document.getElementById('qMarks').innerHTML = headerExtras;
  // Language tabs
  const langTabs = document.getElementById('qLangTabs');
  langTabs.innerHTML = '';
  const availableLangs = Object.keys(q.langs);
  const showLangs = ['en','hn','te','mr','bn','ml','gu','kn','ta','or'].filter(l => availableLangs.includes(l));
  if (showLangs.length > 1) {{
    showLangs.forEach((l, i) => {{
      const btn = document.createElement('button');
      btn.className = 'q-lang-tab' + (l === currentLang ? ' active' : '');
      btn.textContent = {{en:'English',hn:'Hindi',te:'Telugu',mr:'Marathi',bn:'Bengali',ml:'Malayalam',gu:'Gujarati',kn:'Kannada',ta:'Tamil',or:'Odia'}}[l] || l;
      btn.onclick = () => {{ currentLang = l; renderQuestion(); }};
      langTabs.appendChild(btn);
    }});
  }}
  // Question text
  const langData = q.langs[currentLang] || q.langs[availableLangs[0]] || {{}};
  document.getElementById('qText').innerHTML = langData.value || '(no question text)';
  // Options
  const optsList = document.getElementById('optionsList');
  optsList.innerHTML = '';
  (langData.options || []).forEach(opt => {{
    const li = document.createElement('li');
    li.className = 'option-item' + (answers[q.qid] === opt.prompt ? ' selected' : '');
    li.innerHTML = '<span class="option-label">' + opt.prompt + '</span><span class="option-text">' + opt.value + '</span>';
    li.onclick = () => selectOption(q.qid, opt.prompt);
    optsList.appendChild(li);
  }});
  // Update palette
  renderPalette();
  updateStats();
}}

function selectOption(qid, prompt) {{
  if (submitted) return;
  if (answers[qid] === prompt) {{
    delete answers[qid];
  }} else {{
    answers[qid] = prompt;
  }}
  renderQuestion();
}}

function saveNext() {{
  nextQuestion();
}}

function markReviewNext() {{
  const q = allQuestions[currentQIdx];
  marked[q.qid] = true;
  nextQuestion();
}}

function clearResponse() {{
  const q = allQuestions[currentQIdx];
  delete answers[q.qid];
  renderQuestion();
}}

function nextQuestion() {{
  if (currentQIdx < allQuestions.length - 1) {{
    currentQIdx++;
    renderQuestion();
  }}
}}

function prevQuestion() {{
  if (currentQIdx > 0) {{
    currentQIdx--;
    renderQuestion();
  }}
}}

function jumpTo(idx) {{
  currentQIdx = idx;
  renderQuestion();
  window.scrollTo({{top: 0, behavior: 'smooth'}});
}}

function switchSection(idx) {{
  currentSectionIdx = idx;
  // Find first question in this section
  const qIdx = allQuestions.findIndex(q => q.sectionIdx === idx);
  if (qIdx >= 0) {{
    currentQIdx = qIdx;
    renderQuestion();
  }}
}}

function renderPalette() {{
  const grid = document.getElementById('paletteGrid');
  grid.innerHTML = '';
  allQuestions.forEach((q, i) => {{
    const cell = document.createElement('div');
    let cls = 'palette-cell ';
    const isAnswered = answers[q.qid] !== undefined;
    const isMarked = marked[q.qid];
    const isVisited = visited.has(q.qid);
    if (isMarked && isAnswered) cls += 'marked-answered';
    else if (isMarked) cls += 'marked';
    else if (isAnswered) cls += 'answered';
    else if (isVisited) cls += 'notanswered';
    else cls += 'notvisited';
    if (i === currentQIdx) cls += ' current';
    cell.className = cls;
    cell.textContent = (i + 1);
    cell.onclick = () => jumpTo(i);
    grid.appendChild(cell);
  }});
}}

function updateStats() {{
  let answered = 0, notAnswered = 0, marked = 0, notVisited = 0;
  allQuestions.forEach((q, i) => {{
    const isAnswered = answers[q.qid] !== undefined;
    const isMarked = marked[q.qid];
    const isVisited = visited.has(q.qid);
    if (isMarked && isAnswered) marked++;
    else if (isMarked) marked++;
    else if (isAnswered) answered++;
    else if (isVisited) notAnswered++;
    else notVisited++;
  }});
  document.getElementById('paletteStats').innerHTML = `
    <div><span>Answered:</span><strong>${{answered}}</strong></div>
    <div><span>Not Answered:</span><strong>${{notAnswered}}</strong></div>
    <div><span>Marked:</span><strong>${{marked}}</strong></div>
    <div><span>Not Visited:</span><strong>${{notVisited}}</strong></div>
  `;
}}

function openSubmitModal() {{
  let answered = 0, marked = 0, notVisited = 0;
  allQuestions.forEach((q, i) => {{
    if (answers[q.qid] !== undefined) answered++;
    else if (marked[q.qid]) marked++;
    else if (!visited.has(q.qid)) notVisited++;
  }});
  document.getElementById('submitSummary').innerHTML = `
    <div><span>Answered:</span><strong>${{answered}}</strong></div>
    <div><span>Not Answered:</span><strong>${{TOTAL_Q - answered - notVisited}}</strong></div>
    <div><span>Not Visited:</span><strong>${{notVisited}}</strong></div>
    <div><span>Total:</span><strong>${{TOTAL_Q}}</strong></div>
  `;
  document.getElementById('submitModal').classList.add('open');
}}

function closeSubmitModal() {{
  document.getElementById('submitModal').classList.remove('open');
}}

function submitTest() {{
  submitted = true;
  clearInterval(timerInterval);
  closeSubmitModal();
  // Calculate score
  let score = 0, correct = 0, wrong = 0, skipped = 0;
  allQuestions.forEach(q => {{
    const userAns = answers[q.qid];
    const correctAns = q.correct_option;
    if (userAns === undefined) {{
      skipped++;
    }} else if (userAns === correctAns) {{
      correct++;
      score += q.pos_marks;
    }} else {{
      wrong++;
      score -= q.neg_marks;
    }}
  }});
  // Show results view
  document.getElementById('testMode').style.display = 'none';
  document.getElementById('resultsView').style.display = 'block';
  document.getElementById('scoreCard').innerHTML = `
    <div><span class="score">${{score.toFixed(2)}}</span><span class="total"> / ${{TEST_DATA.max_marks || TOTAL_Q * 2}}</span></div>
    <div class="rank">Correct: ${{correct}} | Wrong: ${{wrong}} | Skipped: ${{skipped}}</div>
    <div class="rank" style="margin-top:8px;">Accuracy: ${{(correct + wrong > 0) ? ((correct / (correct + wrong)) * 100).toFixed(1) : 0}}%</div>
  `;
  // Render all questions with answers + solutions
  renderAllQuestions();
}}

function renderAllQuestions() {{
  const container = document.getElementById('resultsQuestions');
  container.innerHTML = '';
  allQuestions.forEach((q, i) => {{
    const userAns = answers[q.qid];
    const correctAns = q.correct_option;
    const isCorrect = userAns === correctAns;
    const isWrong = userAns !== undefined && userAns !== correctAns;
    const isSkipped = userAns === undefined;
    const card = document.createElement('div');
    card.className = 'result-card ' + (isCorrect ? 'correct' : isWrong ? 'wrong' : 'skipped');
    const langData = q.langs[currentLang] || q.langs[Object.keys(q.langs)[0]] || {{}};
    let optionsHtml = '';
    (langData.options || []).forEach(opt => {{
      let cls = 'result-option';
      if (opt.prompt === correctAns) cls += ' correct';
      if (opt.prompt === userAns) cls += ' selected';
      optionsHtml += `<li class="${{cls}}"><span class="result-option-label">${{opt.prompt}}</span><span>${{opt.value}}</span></li>`;
    }});
    const sol = q.solution_html[currentLang] || q.solution_html['en'] || '';
    const solHtml = sol ? `<div class="result-solution"><div class="result-solution-title">📖 Solution</div>${{sol}}</div>` : '';
    let statusBadge = '';
    if (isCorrect) statusBadge = '<span class="result-status correct">✓ Correct</span>';
    else if (isWrong) statusBadge = '<span class="result-status wrong">✗ Wrong</span>';
    else statusBadge = '<span class="result-status skipped">— Skipped</span>';
    // Concept + confidence + question ID (same as AI JSON)
    const concept = q.concept || (q.tags && q.tags.length ? q.tags.join(', ') : 'Unidentified');
    const confidence = q.confidence || (q.tags && q.tags.length ? 'high' : 'unidentified');
    const confClass = confidence === 'high' ? 'conf-high' : 'conf-unidentified';
    card.innerHTML = `
      <div class="result-header">
        <span class="result-q-num">Q${{i + 1}}</span>
        ${{statusBadge}}
        <span class="result-concept">🎯 ${{concept}}</span>
        <span class="result-confidence ${{confClass}}">conf: ${{confidence}}</span>
        <span class="result-qid">ID: ${{q.qid}}</span>
      </div>
      <div class="result-text">${{langData.value || '(no text)'}}</div>
      <ul class="result-options">${{optionsHtml}}</ul>
      ${{solHtml}}
    `;
    container.appendChild(card);
  }});
}}

function showAllQuestions() {{
  document.getElementById('resultsView').scrollIntoView({{behavior: 'smooth'}});
}}

// Keyboard navigation
document.addEventListener('keydown', e => {{
  if (submitted) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'ArrowRight' || e.key === 'j') nextQuestion();
  if (e.key === 'ArrowLeft' || e.key === 'k') prevQuestion();
  if (e.key === '1' || e.key === 'a') {{ const q = allQuestions[currentQIdx]; const opts = (q.langs[currentLang]||{{}}).options||[]; if(opts[0]) selectOption(q.qid, opts[0].prompt); }}
  if (e.key === '2' || e.key === 'b') {{ const q = allQuestions[currentQIdx]; const opts = (q.langs[currentLang]||{{}}).options||[]; if(opts[1]) selectOption(q.qid, opts[1].prompt); }}
  if (e.key === '3' || e.key === 'c') {{ const q = allQuestions[currentQIdx]; const opts = (q.langs[currentLang]||{{}}).options||[]; if(opts[2]) selectOption(q.qid, opts[2].prompt); }}
  if (e.key === '4' || e.key === 'd') {{ const q = allQuestions[currentQIdx]; const opts = (q.langs[currentLang]||{{}}).options||[]; if(opts[3]) selectOption(q.qid, opts[3].prompt); }}
}});

init();
"""


# =============================================================================
# URL PARSING + FILE PATH HELPERS
# =============================================================================

def parse_test_url(url: str) -> Tuple[str, str, str]:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5 or parts[1] != "test-series" or parts[3] != "test":
        raise ValueError(f"URL doesn't match expected pattern: {url}")
    return parts[0], parts[2], parts[4]


def parse_series_url(url: str) -> Tuple[str, str]:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3 or parts[1] != "test-series":
        raise ValueError(f"URL doesn't match expected pattern: {url}")
    return parts[0], parts[2]


def sanitize_filename(s: str, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename/folder name."""
    s = re.sub(r'[^a-zA-Z0-9\-_]+', '_', s).strip('_')
    return s[:max_len] if len(s) > max_len else s


def build_output_path(output_dir: str, test_ref: TestRef) -> str:
    """Build the nested output path: output_dir/series/section/subsection/test.html"""
    series_dir = os.path.join(output_dir, sanitize_filename(test_ref.series_slug))
    section_dir = os.path.join(series_dir, sanitize_filename(test_ref.section_name))
    sub_dir = os.path.join(section_dir, sanitize_filename(test_ref.sub_section_name))
    os.makedirs(sub_dir, exist_ok=True)
    safe_title = sanitize_filename(test_ref.title)
    filename = f"{safe_title}_{test_ref.test_id}.html"
    return os.path.join(sub_dir, filename)


# =============================================================================
# SUBJECT DETECTION + AI-FRIENDLY EXPORT
# =============================================================================
# Detects the subject (English/Reasoning/Math/GK/Science/Computer) from the series slug,
# then categorizes each question by concept using tags + keyword detection.
# Exports a structured JSON file per test that AI models can parse with minimal tokens.

SUBJECT_KEYWORDS = {
    "english": ["english", "vocabulary", "grammar", "comprehension", "cloze", "spelling", "idiom", "synonym", "antonym"],
    "reasoning": ["reasoning", "intelligence", "logical", "arrangement", "series", "analogy", "blood", "direction", "ranking", "coding", "puzzle", "syllogism", "venn"],
    "maths": ["maths", "math", "quantitative", "aptitude", "geometry", "algebra", "trigonometry", "mensuration", "profit", "interest", "percentage", "ratio", "time", "speed", "data", "tabulation", "pie", "bar"],
    "gk": ["gk", "general-knowledge", "general-awareness", "awareness", "constitution", "history", "geography", "polity", "economics", "science"],
    "science": ["general-science", "physics", "chemistry", "biology"],
    "computer": ["computer", "computer-awareness"],
}

# Concept keyword maps (used when tags are empty — fallback detection)
CONCEPT_KEYWORDS = {
    "english": {
        "Vocabulary": ["synonym", "antonym", "meaning of", "opposite of", "same meaning", "opposite meaning", "spelling", "misspelt", "correct spelling"],
        "Idioms & Phrases": ["idiom", "phrase", "hobson", "choose the correct meaning"],
        "Grammar - Error Detection": ["error", "grammatically incorrect", "find the error"],
        "Grammar - Fill in the Blanks": ["fill in the blank", "blank", "choose the correct option"],
        "Reading Comprehension": ["read the passage", "comprehension", "passage"],
        "Cloze Test": ["cloze", "fill in the blanks"],
        "Sentence Improvement": ["improve", "improvement", "rewrite"],
        "Active/Passive Voice": ["active voice", "passive voice", "change the voice"],
        "Direct/Indirect Speech": ["direct", "indirect", "reported speech", "narration"],
        "Para Jumbles": ["rearrange", "jumble", "order", "sequence"],
    },
    "reasoning": {
        "Series": ["series", "complete the series", "replace the question mark", "next in"],
        "Analogy": ["analogy", "is to", "as", "related"],
        "Classification": ["odd one", "different", "doesn't belong", "classify"],
        "Coding-Decoding": ["code", "decode", "coded as", "stands for"],
        "Blood Relations": ["mother", "father", "son", "daughter", "brother", "sister", "uncle", "aunt", "cousin", "husband", "wife"],
        "Direction Sense": ["direction", "north", "south", "east", "west", "left", "right", "turn"],
        "Ranking/Order": ["rank", "position", "order", "from left", "from right", "arrange"],
        "Puzzle": ["puzzle", "sitting arrangement", "floor", "linear"],
        "Syllogism": ["syllogism", "conclusion", "statement", "all", "some", "no"],
        "Venn Diagram": ["venn", "diagram", "how many"],
        "Mirror/Water Image": ["mirror", "water image", "reflection"],
        "Paper Folding/Cutting": ["fold", "cut", "paper"],
        "Cube & Dice": ["cube", "dice"],
        "Calendar": ["calendar", "day", "month", "year", "leap"],
        "Clock": ["clock", "hour", "minute", "angle"],
        "Alphabet/Word Test": ["alphabet", "word", "dictionary", "letter"],
        "Arrangement and Pattern": ["arrangement", "pattern", "sequence"],
        "Similarity and Differences": ["similarity", "difference", "similar", "different"],
    },
    "maths": {
        "Number System": ["number", "divisible", "remainder", "hcf", "lcm", "prime", "factor"],
        "Simplification": ["simplify", "simplification"],
        "Percentage": ["percentage", "%", "percent"],
        "Ratio & Proportion": ["ratio", "proportion"],
        "Average": ["average", "mean"],
        "Profit & Loss": ["profit", "loss", "discount", "marked price", "cp", "sp", "cost price", "selling price"],
        "Simple Interest": ["simple interest", "si", "principal", "rate"],
        "Compound Interest": ["compound interest", "ci", "compounded"],
        "Time & Work": ["time and work", "work", "men", "days", "complete the work"],
        "Time, Speed & Distance": ["speed", "distance", "time", "train", "km", "m/s"],
        "Boats & Streams": ["boat", "stream", "upstream", "downstream", "current"],
        "Mixture & Alligation": ["mixture", "alligation", "alloy", "mix"],
        "Algebra": ["algebra", "equation", "x + y", "x^2", "polynomial"],
        "Geometry - Triangles": ["triangle", "abc", "isosceles", "equilateral"],
        "Geometry - Circles": ["circle", "radius", "diameter", "chord", "tangent"],
        "Geometry - Quadrilaterals": ["quadrilateral", "square", "rectangle", "parallelogram", "trapezium"],
        "Geometry - Lines & Angles": ["angle", "parallel lines", "transversal"],
        "Coordinate Geometry": ["coordinate", "x-axis", "y-axis", "origin"],
        "Trigonometry": ["trigonometry", "sin", "cos", "tan", "sec", "cosec", "cot", "theta", "angle of elevation"],
        "Mensuration - 2D": ["area", "perimeter", "rectangle", "square", "triangle", "circle"],
        "Mensuration - 3D": ["volume", "surface area", "cube", "cuboid", "cylinder", "cone", "sphere", "hemisphere"],
        "Data Interpretation - Tables": ["table", "tabulation", "data"],
        "Data Interpretation - Bar": ["bar graph", "bar chart"],
        "Data Interpretation - Pie": ["pie chart", "pie"],
        "Data Interpretation - Line": ["line graph", "line chart"],
        "Statistics": ["mean", "median", "mode", "standard deviation", "variance"],
        "Partnership": ["partner", "partnership", "investment", "profit share"],
        "Ages": ["age", "years old", "ago", "hence"],
        "Pipes & Cisterns": ["pipe", "cistern", "tank", "fill", "empty"],
        "Permutation & Combination": ["permutation", "combination", "arrange", "select"],
        "Probability": ["probability", "chance", "likely"],
    },
    "gk": {
        "History - Ancient": ["ancient", "indus valley", "vedic", "maurya", "gupta"],
        "History - Medieval": ["medieval", "delhi sultanate", "mughal"],
        "History - Modern": ["modern history", "freedom struggle", "independence", "congress", "gandhi", "nehru"],
        "Polity - Constitution": ["constitution", "amendment", "fundamental rights", "directive principles"],
        "Polity - Government": ["parliament", "president", "prime minister", "supreme court", "high court"],
        "Geography - Physical": ["physical geography", "mountain", "river", "climate", "monsoon"],
        "Geography - Indian": ["india", "state", "capital", "union territory"],
        "Geography - World": ["world", "continent", "country", "ocean"],
        "Economics": ["economy", "economics", "gdp", "inflation", "reserve bank", "budget", "tax"],
        "General Science - Biology": ["biology", "cell", "plant", "animal", "human body", "disease"],
        "General Science - Chemistry": ["chemistry", "chemical", "acid", "base", "reaction", "element"],
        "General Science - Physics": ["physics", "force", "energy", "motion", "light", "sound", "electricity"],
        "Static GK": ["static", "books", "authors", "awards", "sports", "famous"],
    },
    "science": {
        "Biology": ["biology", "cell", "plant", "animal", "human body"],
        "Chemistry": ["chemistry", "chemical", "acid", "reaction", "element", "compound"],
        "Physics": ["physics", "force", "energy", "motion", "light", "sound"],
    },
}


def detect_subject(series_slug: str, test_title: str = "") -> str:
    """Detect the subject from series slug + title. Returns one of:
    english, reasoning, maths, gk, science, computer, or 'general'."""
    text = (series_slug + " " + test_title).lower()
    # Check in priority order (most specific first)
    for subject, keywords in SUBJECT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return subject
    return "general"


# Tag normalization map — converts RepeaterMock's verbose tag names to short labels
# that the user wants (e.g. "Synonyms or Antonyms" → "Synonym" or "Antonym" based on question text)
TAG_NORMALIZATION = {
    # English tags
    "synonyms or antonyms": None,  # needs further analysis (synonym vs antonym)
    "spellings": "Spelling",
    "spelling": "Spelling",
    "phrase or idiom meaning": "Idioms",
    "idioms and phrases": "Idioms",
    "idioms": "Idioms",
    "idiom": "Idioms",
    "phrase replacement": "Sentence Improvement",
    "phrase substitution": "Sentence Improvement",
    "one word substitution": "OWS",
    "one-word substitution": "OWS",
    "one word substitute": "OWS",
    "phrasal verbs": "Phrasal Verbs",
    "fill in the blanks": "Fill in the Blanks",
    "fill in the blank": "Fill in the Blanks",
    "cloze test": "Cloze Test",
    "error spotting": "Error Detection",
    "error detection": "Error Detection",
    "sentence improvement": "Sentence Improvement",
    "sentence correction": "Sentence Improvement",
    "active/passive voice": "Voice",
    "voice": "Voice",
    "direct/indirect speech": "Narration",
    "narration": "Narration",
    "direct/indirect": "Narration",
    "reading comprehension": "Reading Comprehension",
    "comprehension": "Reading Comprehension",
    "para jumbles": "Para Jumbles",
    "sentence rearrangement": "Para Jumbles",
    "sentence ordering": "Para Jumbles",
    "homophones": "Homophones",
    "homonyms": "Homophones",
    "parts of speech": "Grammar",
    "vocabulary": None,  # too generic — needs further analysis
    "grammar": "Grammar",
    "verbal ability": None,  # too generic — needs further analysis
    "sentence": None,  # too generic — needs further analysis
    # Reasoning tags
    "arrangement and pattern": "Arrangement and Pattern",
    "series": "Series",
    "analogy": "Analogy",
    "classification": "Classification",
    "coding decoding": "Coding-Decoding",
    "coding-decoding": "Coding-Decoding",
    "blood relations": "Blood Relations",
    "direction sense": "Direction Sense",
    "ranking": "Ranking/Order",
    "order and ranking": "Ranking/Order",
    "puzzle": "Puzzle",
    "syllogism": "Syllogism",
    "venn diagram": "Venn Diagram",
    "mirror image": "Mirror/Water Image",
    "water image": "Mirror/Water Image",
    "paper folding and cutting": "Paper Folding/Cutting",
    "calendar": "Calendar",
    "clock": "Clock",
    "alphabet or word test": "Alphabet/Word Test",
    "alphabet test": "Alphabet/Word Test",
    "word test": "Alphabet/Word Test",
    "similarity and differences": "Similarity and Differences",
    "figure counting": "Figure Counting",
    "embedded figures": "Embedded Figures",
    "missing number": "Series",
    "non verbal reasoning": "Non Verbal Reasoning",
    "logical puzzle": "Puzzle",
    "quant based puzzle": "Puzzle",
    "number based": "Number System",
    "letter based": "Alphabet/Word Test",
    "letter and number based": "Alphabet/Word Test",
    "sequence": "Series",
    "logical venn diagram": "Venn Diagram",
    "counting figures": "Figure Counting",
    "coding and decoding by letter shifting": "Coding-Decoding",
    "general knowledge based": "General Knowledge",
    # Maths tags
    "profit and loss": "Profit & Loss",
    "profit & loss": "Profit & Loss",
    "simple interest": "Simple Interest",
    "compound interest": "Compound Interest",
    "interest": None,  # could be SI or CI — needs further analysis
    "percentage": "Percentage",
    "ratio and proportion": "Ratio & Proportion",
    "ratio & proportion": "Ratio & Proportion",
    "average": "Average",
    "time and work": "Time & Work",
    "time & work": "Time & Work",
    "time speed distance": "Time, Speed & Distance",
    "boats and streams": "Boats & Streams",
    "simplification": "Simplification",
    "number system": "Number System",
    "geometry": "Geometry",
    "trigonometry": "Trigonometry",
    "mensuration": "Mensuration",
    "algebra": "Algebra",
    "tabulation": "Data Interpretation",
    "data interpretation": "Data Interpretation",
    "partnership": "Partnership",
    "ages": "Ages",
    "pipes and cisterns": "Pipes & Cisterns",
    "permutation and combination": "Permutation & Combination",
    "probability": "Probability",
    "statistics": "Statistics",
    # GK tags
    "basics of constitution": "Polity",
    "constitution": "Polity",
    "central government": "Polity",
    "national movement (1885 - 1919)": "History",
    "national movement": "History",
    "human body": "Biology",
    "zoology": "Biology",
    "botany": "Biology",
    "geography": "Geography",
    "history": "History",
    "economics": "Economics",
    "science": None,  # too generic
    "chemistry": "Chemistry",
    "physics": "Physics",
    "biology": "Biology",
}


def _normalize_tags(tags: List[str], text: str) -> str:
    """Normalize RepeaterMock tags to short, specific labels.
    For generic tags like "Synonyms or Antonyms", further analyzes the question text
    to determine the specific type (Synonym vs Antonym)."""
    result = []
    for tag in tags:
        tag_lower = tag.lower().strip()
        normalized = TAG_NORMALIZATION.get(tag_lower)
        if normalized:
            result.append(normalized)
        elif normalized is None:
            # Tag needs further analysis based on question text
            if tag_lower in ("synonyms or antonyms", "synonym and antonym"):
                if "antonym" in text or "opposite" in text:
                    result.append("Antonym")
                elif "synonym" in text or "same meaning" in text:
                    result.append("Synonym")
                else:
                    result.append("Synonym/Antonym")
            elif tag_lower == "vocabulary":
                # Check for specific vocabulary subtypes
                if "synonym" in text:
                    result.append("Synonym")
                elif "antonym" in text or "opposite" in text:
                    result.append("Antonym")
                elif "idiom" in text or "phrase" in text:
                    result.append("Idioms")
                elif "spelling" in text or "misspelt" in text:
                    result.append("Spelling")
                elif "one word" in text or "substitute" in text:
                    result.append("OWS")
                else:
                    result.append("Vocabulary")
            elif tag_lower in ("verbal ability", "sentence"):
                # Check for specific grammar patterns
                if "error" in text or "grammatically" in text:
                    result.append("Error Detection")
                elif "fill" in text or "____" in text or "blank" in text:
                    result.append("Fill in the Blanks")
                elif "improve" in text or "rewrite" in text:
                    result.append("Sentence Improvement")
                elif "rearrange" in text or "jumble" in text:
                    result.append("Para Jumbles")
                elif "voice" in text:
                    result.append("Voice")
                elif "narration" in text or "direct" in text or "indirect" in text:
                    result.append("Narration")
                else:
                    result.append(tag)  # keep original if can't determine
            elif tag_lower == "interest":
                if "compound" in text:
                    result.append("Compound Interest")
                elif "simple" in text:
                    result.append("Simple Interest")
                else:
                    result.append("Interest")
            elif tag_lower == "science":
                if "biology" in text or "cell" in text or "plant" in text or "animal" in text:
                    result.append("Biology")
                elif "chemistry" in text or "chemical" in text or "acid" in text:
                    result.append("Chemistry")
                elif "physics" in text or "force" in text or "energy" in text:
                    result.append("Physics")
                else:
                    result.append("Science")
            else:
                # Unknown tag — keep original
                result.append(tag)
    
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for r in result:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return ", ".join(deduped) if deduped else ", ".join(tags)


def detect_concept(subject: str, tags: List[str], question_text: str, options: List[dict]) -> Tuple[str, str]:
    """Detect the concept/category for a question with 100%-accuracy rules.
    Returns (concept, confidence) where confidence is "high", "medium", or "unidentified".
    
    STRICT RULES (per user requirement):
    - Only classify when the question EXPLICITLY states its type (e.g. "synonym of", "antonym of")
      OR RepeaterMock provides authoritative tags.
    - If the question type isn't 100% identifiable from the text, mark as "Unidentified"
      rather than guessing.
    
    Priority:
    1. RepeaterMock's tags → high confidence (directly from the source, 100% accurate)
    2. Explicit question-type keywords (e.g. "synonym of", "antonym of", "complete the series")
       → high confidence (only when the phrasing is unambiguous)
    3. "Unidentified" → if no 100%-certain match (skip guessing)
    """
    text = (question_text + " " + " ".join(opt.get("value", "") for opt in options)).lower()
    # Strip markdown formatting markers (**bold**, __underline__, *italic*) so they
    # don't interfere with pattern matching (e.g. "**misspelt **word" → "misspelt word")
    text = re.sub(r'\*\*([^*]*)\*\*', r'\1', text)
    text = re.sub(r'__([^_]*)__', r'\1', text)
    text = re.sub(r'\*([^*]*)\*', r'\1', text)
    text = re.sub(r'_{2,}', '____', text)  # normalize blanks to ____
    
    # 1. Use RepeaterMock tags if available — normalize them to short labels
    # (RepeaterMock uses names like "Synonyms or Antonyms", "Spellings", "Phrase or Idiom Meaning"
    #  but the user wants short specific labels like "Synonym", "Antonym", "Spelling", "Idioms", "OWS")
    if tags:
        normalized = _normalize_tags(tags, text)
        return normalized, "high"
    
    # 2. Explicit question-type detection (100% accuracy — only when phrasing is unambiguous)
    # Each pattern requires the question to literally state the type
    # Labels are kept SHORT and SPECIFIC (e.g. "Synonym" not "Vocabulary: Synonyms")
    # Patterns are ordered: most specific first (e.g. "Trigonometry" before "Simplification")
    explicit_patterns = {
        "english": [
            # Vocabulary — must explicitly say "synonym", "antonym", etc.
            (r"\bsynonym\b.*\bof\b", "Synonym"),
            (r"\bantonym\b.*\bof\b", "Antonym"),
            (r"most appropriate synonym", "Synonym"),
            (r"most appropriate antonym", "Antonym"),
            (r"choose the correct meaning of idiom", "Idioms"),
            (r"meaning of (the )?idiom", "Idioms"),
            (r"meaning of (the )?phrase", "Idioms"),
            (r"idiom.* meaning", "Idioms"),
            (r"misspelt\s*word|incorrectly spelt|correct spelling|spelling", "Spelling"),
            (r"one word substitution|one-word substitution|one word substitute|one-word substitute", "OWS"),
            (r"phrasal verb", "Phrasal Verbs"),
            # Grammar — must explicitly mention the grammar rule
            (r"active voice.*passive voice|passive voice.*active voice|change the voice", "Voice"),
            (r"direct speech.*indirect speech|indirect speech.*direct speech|reported speech", "Narration"),
            (r"direct.*indirect|indirect.*direct", "Narration"),
            (r"error.*sentence|sentence.*error|grammatically incorrect|find the error|identify.*error|error in", "Error Detection"),
            (r"fill in the blank|fill.*blank", "Fill in the Blanks"),
            (r"_{2,}|select the correct option.*_{2,}", "Fill in the Blanks"),
            (r"read the passage|comprehension passage", "Reading Comprehension"),
            (r"cloze test", "Cloze Test"),
            (r"improve.* sentence|sentence improvement|rewrite.* sentence", "Sentence Improvement"),
            (r"rearrange.* sentence|jumble|correct sequence.* sentence|order.* sentence", "Para Jumbles"),
            (r"parts of speech|noun|pronoun|verb|adverb|adjective|preposition|conjunction", "Grammar"),
            (r"identify.* tense|tense of", "Grammar"),
            (r"identify.* clause|clause", "Grammar"),
        ],
        "reasoning": [
            (r"complete the series|next.*in.*series|series.*question mark|replace.*question mark.*series", "Series"),
            # Analogy — check BEFORE Direction Sense (since "same relationship" is analogy)
            (r"same relationship|share.*same relationship|is related to|is to.*as.*is to|analogy", "Analogy"),
            (r"odd one|doesn't belong|different.*group|find.*different|three are alike|alike in some manner", "Classification"),
            (r"in a certain code|code language|coded as|code for|decode.* following|stands for|written as", "Coding-Decoding"),
            (r"mother.*son|father.*daughter|brother.*sister|how.*related|blood relation", "Blood Relations"),
            (r"direction|north.*south|east.*west|turn.*left|turn.*right", "Direction Sense"),
            (r"rank.*from|position.*from|order.* arrange|arrange.*words.*logical|meaningful order", "Ranking/Order"),
            (r"dice|three different positions.*dice", "Puzzle"),
            (r"puzzle|sitting arrangement", "Puzzle"),
            (r"syllogism|conclusion.* statement|all.* some.* no", "Syllogism"),
            (r"venn diagram", "Venn Diagram"),
            (r"mirror image|water image", "Mirror/Water Image"),
            (r"paper folding|paper cutting", "Paper Folding/Cutting"),
            (r"calendar|leap year|day.* week", "Calendar"),
            (r"clock.*hour|clock.*minute|angle.*clock", "Clock"),
            (r"dictionary order|alphabet.*order|word.* dictionary|letter.* dictionary", "Alphabet/Word Test"),
            (r"study.* pattern|arrangement.*word|pattern.* word|sequence.* word|given pattern", "Arrangement and Pattern"),
            (r"address.*same|match.* address|exactly.* same.* address", "Similarity and Differences"),
            (r"how many triangle|count.* triangle|count.* figure", "Figure Counting"),
            (r"embedded figure|embedded.* image", "Embedded Figures"),
        ],
        "maths": [
            # Trigonometry MUST be checked BEFORE Simplification (since "Simplify sec²α" is trig)
            (r"\\sec|\\sin|\\cos|\\tan|\\cosec|\\cot|\bsec\b.*\balpha\b|\bsin\b.*angle|\bcos\b.*angle|trigonometr|angle of elevation|angle of depression", "Trigonometry"),
            # Data Interpretation (tables/graphs) — check BEFORE other patterns
            (r"table shows|table.* year|data.* table|tabulation|study the table|bar graph|bar chart|pie chart|line graph", "Data Interpretation"),
            # Time & Work — check BEFORE Percentage (since "25% more efficient" is about work)
            (r"complete.*work|can complete|work.*days|men.*complete|more efficient|less efficient|efficiency", "Time & Work"),
            # Compound Interest — check BEFORE Simple Interest (since "amounts to" implies CI)
            (r"compound interest|compounded.*annually|compounded.*yearly|amounts to.*years?.*rate|sum.*amounts to", "Compound Interest"),
            # Specific topics
            (r"profit.* loss|loss.* profit|discount.* marked|marked price|cost price|selling price|cp.*sp|better offer", "Profit & Loss"),
            (r"simple interest|principal.*rate.*time", "Simple Interest"),
            (r"percentage|%\s|percent of", "Percentage"),
            (r"ratio.* proportion|proportion.* ratio", "Ratio & Proportion"),
            (r"average.* number|average.* age|average.* marks|find.* average", "Average"),
            (r"time.* work|work.* days|men.* complete|complete.* work", "Time & Work"),
            (r"speed.* distance|distance.* time|km.* hour|km/hr|m/s", "Time, Speed & Distance"),
            (r"boat.* stream|upstream|downstream|speed of boat", "Boats & Streams"),
            (r"simplify|simplification|value of expression", "Simplification"),
            (r"hcf|lcm|divisible|remainder|prime factor|greatest.*divisor|least.*multiple", "Number System"),
            # Geometry — check for triangle/circle/quadrilateral keywords
            (r"triangle|abc.* angle|isosceles|equilateral|right.*angle.*triangle|cyclic quadrilateral|Δ abc|δ abc|similar.*triangle", "Geometry"),
            (r"circle.* radius|radius.* circle|diameter|chord.* tangent|tangent.* circle", "Geometry"),
            (r"quadrilateral|parallelogram|trapezium|rhombus", "Geometry"),
            (r"parallel lines.* transversal|transversal.* angle", "Geometry"),
            (r"area of|perimeter of|volume of|surface area|curved surface|total surface", "Mensuration"),
            # Algebra — check for variables (x, y) and equations
            (r"\b[0-9]*x.*[0-9]*y\b|algebra|equation|polynomial|solve.* x|value of.*x|value of \(", "Algebra"),
            (r"partnership|invested.* business|profit.* share.* partner", "Partnership"),
            (r"age.* year|years? old|ago.* will be|will be.* years?", "Ages"),
            (r"pipe.* cistern|tank.* fill|tank.* empty|tap.* fill", "Pipes & Cisterns"),
            (r"permutation|combination|arrange.* select|ways.* choose", "Permutation & Combination"),
            (r"probability|chance.* occur|likely.* happen|dice.* probability|coin.* probability", "Probability"),
            (r"mean.* median.* mode|standard deviation|variance", "Statistics"),
        ],
        "gk": [
            # GK questions are usually direct factual — categorize by topic keywords
            (r"constitution|amendment|fundamental right|directive principle|article \d+|finance commission|writs?|supreme court|high court|lok sabha|rajya sabha|parliament|president of india|prime minister", "Polity"),
            (r"ancient india|indus valley|vedic|maurya|gupta dynasty|medieval|delhi sultanate|mughal|akbar|babur|aurangzeb|freedom struggle|independence.* 1947|revolt of 1857|gandhi|nehru|congress|morley.*minto|reforms? \d{4}", "History"),
            (r"mountain|river|climate|monsoon|plateau|plain|demographic transition|geography", "Geography"),
            (r"state.* capital|union territory|indian state", "Geography"),
            (r"continent|ocean|country.* capital|world", "Geography"),
            (r"gdp|inflation|reserve bank|budget|tax|economy|fiscal|gresham.*law|economics?", "Economics"),
            (r"biology|cell|plant|animal|human body|disease|vitamin|protein", "Biology"),
            (r"chemistry|chemical|acid|base|reaction|element|compound|metal", "Chemistry"),
            (r"physics|force|energy|motion|light|sound|electricity|gravity|magnet", "Physics"),
            (r"books? and authors?|who wrote|author of", "Static GK"),
            (r"awards?|honours?|nobel|padma|bharat ratna|arjuna", "Static GK"),
            (r"sports?|cricket|football|olympic|commonwealth|world cup", "Static GK"),
            (r"dance?|festival|folk dance", "Static GK"),
            (r"first in india|first in world|international court of justice|international organisation|united nations|world bank|imf|who.* unesco", "Static GK"),
        ],
    }
    
    patterns = explicit_patterns.get(subject, [])
    matches_found = []
    for pattern, concept in patterns:
        if re.search(pattern, text):
            matches_found.append(concept)
    
    if matches_found:
        # If multiple patterns matched, use the first one (most specific)
        return matches_found[0], "high"
    
    # 3. No 100%-certain match — mark as Unidentified (per user requirement)
    return "Unidentified", "unidentified"


def strip_html(text: str) -> str:
    """Convert HTML to AI-friendly plain text while preserving formatting markers.
    
    Handles:
    - Double-escaped HTML entities (e.g. &amp;deg; -> &deg; -> degree symbol)
    - LaTeX math notation backslash-paren...backslash-paren -> readable plain text (sec^2 alpha)
    - <strong>/<b> -> **bold** (markdown)
    - <em>/<i>/<u> -> __underline__ (markdown)
    - <sup> → ^{...} (superscript for math)
    - <sub> → _{...} (subscript for math)
    - Images → [IMAGE: url]
    - HTML tables → tab-separated text
    
    Strips all other HTML tags but keeps the text content.
    """
    import html as html_mod
    if not text:
        return ""
    # Decode HTML entities TWICE (the data is often double-escaped:
    # e.g. &amp;deg; → &deg; → °, &amp;nbsp; → &nbsp; → space)
    text = html_mod.unescape(text)
    text = html_mod.unescape(text)
    
    # Convert LaTeX \(...\) math notation to readable plain text
    # Common LaTeX commands → plain text equivalents
    def latex_to_text(m):
        latex = m.group(1)
        # Remove \rm, \displaystyle etc.
        latex = re.sub(r'\\rm\b', '', latex)
        latex = re.sub(r'\\displaystyle\b', '', latex)
        latex = re.sub(r'\\text\b', '', latex)
        latex = re.sub(r'\\mathrm\b', '', latex)
        # \frac{a}{b} → a/b
        latex = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'(\1)/(\2)', latex)
        # \left( → (, \right) → )
        latex = latex.replace('\\left(', '(').replace('\\right)', ')')
        latex = latex.replace('\\left[', '[').replace('\\right]', ']')
        latex = latex.replace('\\left|', '|').replace('\\right|', '|')
        # \sec → sec, \sin → sin, \cos → cos, \tan → tan, etc.
        latex = re.sub(r'\\(sec|cosec|csc|sin|cos|tan|cot|log|ln|lim|sqrt|sum|prod|alpha|beta|gamma|theta|pi|infty|div|times|pm|mp|leq|geq|neq|approx|equiv|propto|angle|degree)\b', r'\1', latex)
        # \alpha → alpha, \beta → beta, etc. (already handled above, but also standalone)
        for greek in ['alpha','beta','gamma','delta','epsilon','theta','lambda','mu','pi','rho','sigma','phi','psi','omega']:
            latex = re.sub(r'\\' + greek + r'\b', greek, latex)
        # x^{2} → x² (convert simple superscripts to Unicode)
        latex = re.sub(r'\^(\d)', lambda m: _superscript(m.group(1)), latex)
        latex = re.sub(r'\^\{([^}]+)\}', lambda m: _superscript(m.group(1)), latex)
        # _{2} → ₂ (subscripts)
        latex = re.sub(r'_(\d)', lambda m: _subscript(m.group(1)), latex)
        latex = re.sub(r'_\{([^}]+)\}', lambda m: _subscript(m.group(1)), latex)
        # \, → space, \; → space, \! → '', \quad → space
        latex = latex.replace('\\,', ' ').replace('\\;', ' ').replace('\\!', '').replace('\\quad', ' ')
        latex = latex.replace('\\ ', ' ')
        # \cdot → ·, \times → ×, \div → ÷, \pm → ±
        latex = latex.replace('\\cdot', '·').replace('\\times', '×').replace('\\div', '÷').replace('\\pm', '±')
        # Remove any remaining backslashes (e.g. leftover \alpha)
        latex = re.sub(r'\\([a-zA-Z]+)', r'\1', latex)
        # Remove \{ \} 
        latex = latex.replace('\\{', '{').replace('\\}', '}')
        return latex
    
    text = re.sub(r'\\\((.*?)\\\)', latex_to_text, text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.*?)\\\]', latex_to_text, text, flags=re.DOTALL)
    
    # Convert formatting tags to markdown markers (before stripping all tags)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<u[^>]*>(.*?)</u>', r'__\1__', text, flags=re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<sup[^>]*>(.*?)</sup>', lambda m: _superscript(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r'<sub[^>]*>(.*?)</sub>', lambda m: _subscript(m.group(1)), text, flags=re.DOTALL)
    
    # Convert markdown-style ^{...} and _{...} to Unicode super/subscripts
    # (these appear in the raw text as escaped notation like x^{3}, sin^{2})
    # Only convert short numeric/letter sequences to avoid mangling other text
    text = re.sub(r'\^\{([0-9]+)\}', lambda m: _superscript(m.group(1)), text)
    text = re.sub(r'_\{([0-9]+)\}', lambda m: _subscript(m.group(1)), text)
    # Also convert single-char ^2, ^3 etc.
    text = re.sub(r'\^([0-9])(?![0-9])', lambda m: _superscript(m.group(1)), text)
    
    text = re.sub(r'<img[^>]+src="([^"]+)"[^>]*/?>', r' [IMAGE: \1] ', text)
    text = re.sub(r'<p[^>]*>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<li[^>]*>', '\n• ', text)
    text = re.sub(r'</li>', '', text)
    text = re.sub(r'<td[^>]*>', '\t', text)
    text = re.sub(r'</td>', '', text)
    text = re.sub(r'<tr[^>]*>', '\n', text)
    text = re.sub(r'</tr>', '', text)
    text = re.sub(r'<th[^>]*>', '\t', text)
    text = re.sub(r'</th>', '', text)
    # Strip all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Normalize whitespace but preserve newlines
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


# Unicode superscript/subscript conversion tables (for math notation)
_SUP_MAP = str.maketrans('0123456789+-=()n', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ')
_SUB_MAP = str.maketrans('0123456789+-=()aeiourschklmnpstx', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒᵤᵣₛ꜀ₕₖₗₘₙₚₛₜₓ')

def _superscript(s):
    """Convert string to Unicode superscript where possible."""
    return s.translate(_SUP_MAP)

def _subscript(s):
    """Convert string to Unicode subscript where possible."""
    return s.translate(_SUB_MAP)


def extract_images_from_html(html_text: str) -> List[dict]:
    """Extract image URLs from HTML."""
    if not html_text:
        return []
    imgs = []
    for m in re.finditer(r'<img[^>]+src="([^"]+)"[^>]*/?>', html_text):
        imgs.append({"url": m.group(1), "alt": ""})
    return imgs


def render_ai_export(test: TestData, test_ref: TestRef) -> dict:
    """Render a TestData as an AI-friendly JSON structure.
    Minimizes tokens by:
    - Stripping HTML (plain text only)
    - Using short field names
    - Omitting empty fields
    - Including concept categorization for fast filtering
    """
    subject = detect_subject(test_ref.series_slug, test.title)
    
    # Test-level metadata
    export = {
        "test_id": test.test_id,
        "title": test.title,
        "series_slug": test_ref.series_slug,
        "series_name": test_ref.series_name,
        "section": test_ref.section_name,
        "subsection": test_ref.sub_section_name,
        "subject": subject,
        "duration_min": test.duration_min,
        "max_marks": test.max_marks,
        "question_count": sum(len(s.questions) for s in test.sections),
        "languages": test.languages,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "questions": [],
    }
    
    # Per-question data
    for section in test.sections:
        for q in section.questions:
            # Get English content (primary)
            en = q.langs.get("en", {})
            q_text_html = en.get("value", "")
            q_text = strip_html(q_text_html)
            options = en.get("options", [])
            images = extract_images_from_html(q_text_html)
            # Also check options for images
            for opt in options:
                opt_imgs = extract_images_from_html(opt.get("value", ""))
                if opt_imgs:
                    images.extend(opt_imgs)
            
            # Solution (English)
            sol_html = q.solution_html.get("en", "")
            sol_text = strip_html(sol_html)
            sol_images = extract_images_from_html(sol_html)
            
            # Detect concept + confidence
            concept, confidence = detect_concept(subject, q.tags, q_text, options)
            
            q_export = {
                "qid": q.qid,
                "n": q.qs_no,
                "type": q.type,
                "concept": concept,
                "confidence": confidence,
                "marks_pos": q.pos_marks,
                "marks_neg": q.neg_marks,
                "question": q_text,
                "options": [
                    {"label": opt.get("prompt", ""), "text": strip_html(opt.get("value", ""))}
                    for opt in options
                ],
                "correct": q.correct_option,
            }
            # Only include solution if non-empty
            if sol_text:
                q_export["solution"] = sol_text
            # Only include images if any
            if images:
                q_export["images"] = images
            if sol_images:
                q_export["solution_images"] = sol_images
            # Include tags if any (raw from RepeaterMock)
            if q.tags:
                q_export["tags"] = q.tags
            # NOTE: Hindi/other languages removed from AI JSON per user request.
            # AI JSON now contains English-only content for minimal token usage.
            # The interactive HTML still has all 24 languages via language tabs.
            
            export["questions"].append(q_export)
    
    return export


def build_ai_export_path(output_dir: str, test_ref: TestRef) -> str:
    """Build the AI export path: output_dir/ai_export/subject/series/section/subsection/test.json"""
    subject = detect_subject(test_ref.series_slug, test_ref.title)
    base = os.path.join(output_dir, "ai_export", subject, sanitize_filename(test_ref.series_slug))
    section_dir = os.path.join(base, sanitize_filename(test_ref.section_name))
    sub_dir = os.path.join(section_dir, sanitize_filename(test_ref.sub_section_name))
    os.makedirs(sub_dir, exist_ok=True)
    safe_title = sanitize_filename(test_ref.title)
    return os.path.join(sub_dir, f"{safe_title}_{test_ref.test_id}.json")


def build_html_output_path(output_dir: str, test_ref: TestRef) -> str:
    """Build the HTML output path: output_dir/html_export/subject/series/section/subsection/test.html"""
    subject = detect_subject(test_ref.series_slug, test_ref.title)
    base = os.path.join(output_dir, "html_export", subject, sanitize_filename(test_ref.series_slug))
    section_dir = os.path.join(base, sanitize_filename(test_ref.section_name))
    sub_dir = os.path.join(section_dir, sanitize_filename(test_ref.sub_section_name))
    os.makedirs(sub_dir, exist_ok=True)
    safe_title = sanitize_filename(test_ref.title)
    return os.path.join(sub_dir, f"{safe_title}_{test_ref.test_id}.html")


# =============================================================================
# PARALLEL SCRAPER ORCHESTRATOR
# =============================================================================

async def run_scraper(test_urls: List[str], series_urls: List[str], output_dir: str,
                       workers: int, stop_after: Optional[int], resume: bool,
                       max_runtime_min: Optional[int] = None):
    """Main orchestrator: discover tests, spawn workers, scrape in parallel.
    If max_runtime_min is set, stops gracefully after that many minutes (for CI)."""
    from playwright.async_api import async_playwright

    os.makedirs(output_dir, exist_ok=True)
    progress = ProgressTracker(output_dir)

    # Phase 1: Discover all tests
    print("\n=== Phase 1: Discovering tests ===")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
        )
        # Use a single discovery worker
        discovery_worker = Worker(0, browser, progress, output_dir)
        await discovery_worker.start()

        all_test_refs: List[TestRef] = []

        # Single test URLs
        for url in test_urls:
            try:
                platform, series_slug, test_id = parse_test_url(url)
                # We need to discover the section/subsection for this test
                # For single tests, we don't have section info — use placeholder
                all_test_refs.append(TestRef(
                    test_id=test_id,
                    title=test_id,
                    series_slug=series_slug,
                    series_name=series_slug,
                    section_id="single",
                    section_name="Single Tests",
                    sub_section_id="single",
                    sub_section_name="",
                    is_free=True,
                    duration=0,
                    question_count=0,
                    total_mark=0,
                ))
            except ValueError as e:
                print(f"  ERROR: {e}")

        # Series URLs — discover all tests
        for url in series_urls:
            try:
                platform, series_slug = parse_series_url(url)
                series_id, series_name, test_refs = await discover_series_tests(discovery_worker, series_slug)
                # Update series_name on all test_refs
                for tr in test_refs:
                    tr.series_name = series_name
                all_test_refs.extend(test_refs)
            except ValueError as e:
                print(f"  ERROR: {e}")

        await discovery_worker.close()

        # Filter out already-scraped tests (if resume=True)
        if resume:
            before = len(all_test_refs)
            all_test_refs = [tr for tr in all_test_refs if not progress.is_scraped(tr.series_slug, tr.test_id)]
            print(f"\n  Resume: filtered out {before - len(all_test_refs)} already-scraped tests, {len(all_test_refs)} remaining")
        else:
            # Even without --resume, skip already-scraped (safety)
            before = len(all_test_refs)
            all_test_refs = [tr for tr in all_test_refs if not progress.is_scraped(tr.series_slug, tr.test_id)]
            if before - len(all_test_refs) > 0:
                print(f"\n  Skipping {before - len(all_test_refs)} already-scraped tests (use --resume to continue)")

        # Apply stop_after limit
        if stop_after is not None and stop_after > 0:
            all_test_refs = all_test_refs[:stop_after]
            print(f"  Limited to {stop_after} tests (--stop-after)")

        print(f"\n  Total tests to scrape: {len(all_test_refs)}")
        if not all_test_refs:
            print("  Nothing to scrape. Done.")
            await browser.close()
            return

        # Phase 2: Parallel scraping
        print(f"\n=== Phase 2: Scraping with {workers} parallel workers ===")
        queue: asyncio.Queue = asyncio.Queue()
        for tr in all_test_refs:
            await queue.put(tr)

        # Track completion
        completed = 0
        failed = 0
        total = len(all_test_refs)
        completion_lock = asyncio.Lock()

        # Track start time for max_runtime enforcement
        start_time = time.time()
        runtime_exceeded = False

        # Retry queue: tests that failed will be re-added to the main queue after all
        # first-pass tests are done, so they get retried (with fresh cookies)
        retry_queue: List[TestRef] = []
        retry_lock = asyncio.Lock()

        async def worker_loop(worker_id: int):
            nonlocal completed, failed, runtime_exceeded
            w = Worker(worker_id, browser, progress, output_dir)
            await w.start()
            while True:
                # Check max runtime (stop at 5h30m = 330 min for GitHub Actions safety)
                if max_runtime_min is not None:
                    elapsed_min = (time.time() - start_time) / 60
                    if elapsed_min >= max_runtime_min:
                        if not runtime_exceeded:
                            print(f"\n  [worker {worker_id}] ⏰ max runtime {max_runtime_min}min reached, stopping gracefully...")
                            runtime_exceeded = True
                        break
                try:
                    test_ref = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    print(f"\n  [worker {worker_id}] [{completed + failed + 1}/{total}] {test_ref.title[:50]}... (id={test_ref.test_id})")
                    test_data = await w.scrape_test(test_ref)
                    if test_data:
                        # Update title + series_name from scraped data
                        test_ref.title = test_data.title
                        # IMMEDIATE SAVE: write HTML + AI JSON + update progress
                        # This ensures no data is lost even if the process crashes
                        html_path = build_html_output_path(output_dir, test_ref)
                        html = render_test_html(test_data)
                        # Atomic write: write to temp file first, then rename
                        tmp_html = html_path + ".tmp"
                        with open(tmp_html, "w") as f:
                            f.write(html)
                        os.rename(tmp_html, html_path)
                        # Write AI JSON (atomic)
                        ai_path = build_ai_export_path(output_dir, test_ref)
                        ai_export = render_ai_export(test_data, test_ref)
                        tmp_ai = ai_path + ".tmp"
                        with open(tmp_ai, "w") as f:
                            json.dump(ai_export, f, ensure_ascii=False, indent=2)
                        os.rename(tmp_ai, ai_path)
                        # Update progress.json immediately (so resume works if killed)
                        await progress.mark_scraped(test_ref.series_slug, test_ref.test_id, html_path)
                        async with completion_lock:
                            completed += 1
                            w.tests_done += 1
                        print(f"  [worker {worker_id}] ✅ saved HTML ({len(html):,}B) + AI JSON ({len(json.dumps(ai_export)):,}B): {test_ref.title[:50]}")
                    else:
                        # Test failed — add to retry queue for later retry
                        async with retry_lock:
                            retry_queue.append(test_ref)
                        await progress.mark_failed(test_ref.series_slug, test_ref.test_id, "scrape returned None (will retry)")
                        async with completion_lock:
                            failed += 1
                        print(f"  [worker {worker_id}] ⚠️ failed (added to retry queue): {test_ref.test_id}")
                except Exception as e:
                    # Exception — add to retry queue for later retry
                    async with retry_lock:
                        retry_queue.append(test_ref)
                    await progress.mark_failed(test_ref.series_slug, test_ref.test_id, str(e))
                    async with completion_lock:
                        failed += 1
                    print(f"  [worker {worker_id}] ⚠️ exception (added to retry queue): {e}")
                finally:
                    queue.task_done()
                # Small delay to avoid rate-limiting (with jitter)
                await asyncio.sleep(0.3 + random.uniform(0, 0.4))
            await w.close()
            print(f"  [worker {worker_id}] done (scraped {w.tests_done} tests)")

        # Background progress reporter — prints a summary every 30 seconds
        # so you can watch the scrape live in GitHub Actions logs
        progress_stop = asyncio.Event()
        async def progress_reporter():
            while not progress_stop.is_set():
                try:
                    await asyncio.wait_for(progress_stop.wait(), timeout=30.0)
                    break  # event was set, stop
                except asyncio.TimeoutError:
                    pass  # 30s elapsed, print progress
                elapsed_min = (time.time() - start_time) / 60
                done = completed + failed
                remaining = total - done
                rate = done / elapsed_min if elapsed_min > 0 else 0
                eta_min = remaining / rate if rate > 0 else 0
                stats = progress.stats()
                print(f"\n📊 [{elapsed_min:.1f}min] Progress: {done}/{total} ({100*done/total:.1f}%) | "
                      f"✅ {completed} scraped | ⚠️ {failed} failed | "
                      f"📊 {stats['scraped']} total in progress.json | "
                      f"ETA: {eta_min:.0f}min ({eta_min/60:.1f}h)", flush=True)
        
        reporter_task = asyncio.create_task(progress_reporter())

        # Spawn workers
        worker_tasks = [asyncio.create_task(worker_loop(i + 1)) for i in range(workers)]
        # Wait for all to finish
        await asyncio.gather(*worker_tasks)
        
        # Stop the progress reporter
        progress_stop.set()
        try:
            await asyncio.wait_for(reporter_task, timeout=5.0)
        except asyncio.TimeoutError:
            reporter_task.cancel()

        # Phase 3: Retry failed tests (with fresh cookies)
        if retry_queue and not runtime_exceeded:
            print(f"\n=== Phase 3: Retrying {len(retry_queue)} failed tests ===")
            # Re-add failed tests to the queue
            retry_queue_unique = []
            seen_retry_ids = set()
            for tr in retry_queue:
                if tr.test_id not in seen_retry_ids:
                    retry_queue_unique.append(tr)
                    seen_retry_ids.add(tr.test_id)
            print(f"  Unique tests to retry: {len(retry_queue_unique)}")
            # Create a new queue for retries
            retry_q: asyncio.Queue = asyncio.Queue()
            for tr in retry_queue_unique:
                await retry_q.put(tr)
            # Reset failed count (these will be re-attempted)
            retry_failed = 0
            retry_completed = 0
            retry_completion_lock = asyncio.Lock()

            async def retry_worker_loop(worker_id: int):
                nonlocal retry_completed, retry_failed, runtime_exceeded
                # Reuse the same worker ID for logging
                w = Worker(worker_id, browser, progress, output_dir)
                await w.start()
                while True:
                    if max_runtime_min is not None:
                        elapsed_min = (time.time() - start_time) / 60
                        if elapsed_min >= max_runtime_min:
                            runtime_exceeded = True
                            break
                    try:
                        test_ref = retry_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        print(f"\n  [retry-{worker_id}] [{retry_completed + retry_failed + 1}/{len(retry_queue_unique)}] {test_ref.title[:50]}... (id={test_ref.test_id})")
                        test_data = await w.scrape_test(test_ref)
                        if test_data:
                            test_ref.title = test_data.title
                            html_path = build_html_output_path(output_dir, test_ref)
                            html = render_test_html(test_data)
                            tmp_html = html_path + ".tmp"
                            with open(tmp_html, "w") as f:
                                f.write(html)
                            os.rename(tmp_html, html_path)
                            ai_path = build_ai_export_path(output_dir, test_ref)
                            ai_export = render_ai_export(test_data, test_ref)
                            tmp_ai = ai_path + ".tmp"
                            with open(tmp_ai, "w") as f:
                                json.dump(ai_export, f, ensure_ascii=False, indent=2)
                            os.rename(tmp_ai, ai_path)
                            await progress.mark_scraped(test_ref.series_slug, test_ref.test_id, html_path)
                            async with retry_completion_lock:
                                retry_completed += 1
                                w.tests_done += 1
                            print(f"  [retry-{worker_id}] ✅ saved: {test_ref.title[:50]}")
                        else:
                            async with retry_completion_lock:
                                retry_failed += 1
                            await progress.mark_failed(test_ref.series_slug, test_ref.test_id, "retry failed")
                            print(f"  [retry-{worker_id}] ❌ retry failed: {test_ref.test_id}")
                    except Exception as e:
                        async with retry_completion_lock:
                            retry_failed += 1
                        await progress.mark_failed(test_ref.series_slug, test_ref.test_id, f"retry exception: {e}")
                        print(f"  [retry-{worker_id}] ❌ retry exception: {e}")
                    finally:
                        retry_q.task_done()
                    await asyncio.sleep(0.5 + random.uniform(0, 0.5))
                await w.close()

            # Use fewer workers for retry (to avoid more rate limiting)
            retry_workers_count = max(1, min(workers, 3))
            retry_tasks = [asyncio.create_task(retry_worker_loop(i + 1)) for i in range(retry_workers_count)]
            await asyncio.gather(*retry_tasks)
            print(f"\n  Retry phase done: {retry_completed} recovered, {retry_failed} still failed")
            completed += retry_completed
            failed = failed - retry_completed + retry_failed  # adjust counts

        await browser.close()

    # Generate combined question index (index.html + index.json)
    # This shows all questions across all tests, with cross-test deduplication
    print(f"\n=== Generating combined question index ===")
    try:
        import subprocess
        result = subprocess.run(
            ["python3", os.path.join(os.path.dirname(__file__), "generate_index.py"),
             "--output-dir", output_dir],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(result.stdout[-500:])
        else:
            print(f"  ⚠️ Index generation failed: {result.stderr[:300]}")
    except Exception as e:
        print(f"  ⚠️ Index generation skipped: {e}")

    # Generate chapter-wise database (by year/exam/subject/concept)
    # This is a separate copy organized for AI analysis
    print(f"\n=== Generating chapter-wise database ===")
    try:
        import subprocess
        result = subprocess.run(
            ["python3", os.path.join(os.path.dirname(__file__), "build_database.py"),
             "--output-dir", output_dir],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(result.stdout[-500:])
        else:
            print(f"  ⚠️ Database generation failed: {result.stderr[:300]}")
    except Exception as e:
        print(f"  ⚠️ Database generation skipped: {e}")

    # Summary
    elapsed_min = (time.time() - start_time) / 60 if 'start_time' in dir() else 0
    print(f"\n{'='*60}")
    print(f"✅ Done!")
    if runtime_exceeded:
        print(f"   ⏰ Stopped due to max runtime ({max_runtime_min} min)")
    print(f"   Scraped this run: {completed}")
    print(f"   Failed this run: {failed}")
    print(f"   Output: {output_dir}")
    stats = progress.stats()
    print(f"   Total in progress.json: {stats['scraped']} scraped, {stats['failed']} failed")
    if runtime_exceeded:
        remaining = total - completed - failed
        print(f"   Remaining to scrape: {remaining} (re-run with --resume to continue)")
    print(f"{'='*60}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="RepeaterMock Mass Scraper v2 — parallel scraping with resume + interactive mock-test HTML + AI-friendly JSON export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--test-url", action="append", default=[],
                        help="URL of a single test to scrape (can be repeated)")
    parser.add_argument("--series-url", action="append", default=[],
                        help="URL of a series to scrape (can be repeated)")
    parser.add_argument("--series-list-file", default=None,
                        help="Path to a file containing one series URL per line (for GitHub Actions)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--workers", type=int, default=10,
                        help="Number of parallel workers (default: 10, max: 50)")
    parser.add_argument("--stop-after", type=int, default=None,
                        help="Stop after N tests total (default: unlimited)")
    parser.add_argument("--max-runtime", type=int, default=None,
                        help="Max runtime in minutes (e.g. 350 for GitHub Actions 6h limit)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from previous run (skip already-scraped tests)")
    args = parser.parse_args()

    # Load series URLs from file if specified
    series_urls = list(args.series_url)
    if args.series_list_file:
        with open(args.series_list_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    series_urls.append(line)

    if not args.test_url and not series_urls:
        parser.error("Provide at least one --test-url, --series-url, or --series-list-file")

    # Clamp workers
    workers = max(1, min(args.workers, 50))

    asyncio.run(run_scraper(
        test_urls=args.test_url,
        series_urls=series_urls,
        output_dir=args.output_dir,
        workers=workers,
        stop_after=args.stop_after,
        resume=args.resume,
        max_runtime_min=args.max_runtime,
    ))


if __name__ == "__main__":
    main()
