# RepeaterMock Mass Scraper

Download ALL free mock tests from RepeaterMock as interactive HTML files — **no login needed**.

## Quick Start

```bash
pip install -r requirements.txt
playwright install chromium
python mass_scraper.py
```

## What It Does

1. Visits repeatermock.com (gets guest cookies — no login)
2. Fetches all test lists for 20+ free test series (SSC, RRB, SBI)
3. For each test: fetches questions, starts attempt, submits, gets solutions + analysis
4. Resolves RSC $N references (solutions stored as $32, $57 etc.)
5. Saves each test as an interactive HTML file (RepeaterMock clone)
6. Organizes files in folders: exam_name/year/category/test_title.html
7. Auto-resumes from where it left off (reads progress.json)
8. Runs continuously until all tests are done

## Folder Structure

```
output/
  SSC CGL/
    2025/
      Previous Year Paper/
        SSC CGL 2025 Shift 1.html
      PYST/
        General Awareness Test 1.html
  SSC CHSL/
    2026/
      Full Length Test/
        CHSL Mock Test 1.html
  RRB Group D/
    2025-26/
      PYST/
        RRB Group D Maths Test 1.html
```

## Features

- **No login**: Guest mode only — visits homepage, gets cookies
- **RSC reference resolution**: Solves $N references ($32 → actual solution text with images)
- **Interactive HTML**: Take test → submit → see score + solutions + rank
- **MathJax**: Renders math expressions in questions and solutions
- **CDN images**: Kept as links (not downloaded)
- **Auto-resume**: Reads progress.json, skips already-scraped tests
- **Concurrent**: Downloads 5 tests simultaneously for speed
- **Rate limiting**: Caps 429 retries at 120s, max 3 retries per test
- **Progress tracking**: Saves progress every 5 tests

## Test Series (FREE — no login)

### SSC
- SSC CGL, CHSL, CPO, MTS, GD Constable, Selection Post, Stenographer, JE Civil, JE Electrical
- SSC Maths/Reasoning/English/GK PYP (20k+ questions each)

### RRB / Railway
- RRB Group D, RRB GK PYP, RRB General Science PYP

### Banking
- SBI PO (Guidely platform)

## API Flow

```
1. GET repeatermock.com (guest cookies)
2. GET /api/v1/test-series/{slug} (series details)
3. GET /api/v1/test-series/{id}/sections/{secId}/tests (test list)
4. GET /tb/test-series/{slug}/test/{testId}/attempt (questions from RSC)
5. POST /api/v1/attempts/{testId}/start (create attempt)
6. POST /api/v1/attempts/{testId}/submit (submit empty)
7. GET /tb/test-series/{slug}/test/{testId}/solution (answers + solutions from RSC)
8. GET /tb/test-series/{slug}/test/{testId}/analysis (rank from RSC)
```

## RSC Reference Resolution

RepeaterMock stores solutions as React Server Component references:
```
sol.en.value = "$32"  ← This is a reference ID, NOT the actual solution
```

The actual solution text is defined elsewhere in the RSC flight payload:
```
32:T596,<p><img src="cdn.repeatermock.com/..."> The arrangement...</p>
```

The scraper builds a reference table from all `N:T<type>,<content>` patterns and resolves `$N` → actual content.
