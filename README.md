# RepeaterMock Mass Scraper v2

Scrapes **ALL free tests** from any RepeaterMock test series — gets every question, correct answer, and full written solution — and renders each test as a **fully interactive mock-test HTML page** (countdown timer, question palette, Save & Next / Mark for Review / Clear Response, Submit → reveals all answers + solutions + score). Looks and works just like the real RepeaterMock website.

**No login required.** Free tests work as a guest (20 free attempts per test).

## Features

- **Parallel scraping**: configurable workers (default 10, up to 50). Each worker is an isolated browser context with its own cookie jar + Cloudflare clearance.
- **Auto cookie refresh**: detects 401 responses AND network errors, automatically re-navigates to repeatermock.com to refresh Cloudflare clearance + cookies, then retries. Also refreshes proactively every 15 minutes.
- **Resume capability**: saves progress to `progress.json` after every test. If you kill the terminal and restart, it picks up exactly where it left off — no duplicate scrapes.
- **Nested folder structure**: `output_dir/series_slug/section_name/subsection_name/test_title.html` — organized exactly like the website's hierarchy.
- **Stop after N**: `--stop-after 50` stops after 50 tests total.
- **Interactive mock-test UI**: each HTML file is a real interactive test — countdown timer (classic mode), question palette with color-coded statuses (Answered / Not Answered / Marked / Not Visited), Save & Next / Mark for Review & Next / Clear Response buttons, Submit Test → confirmation modal → answer reveal with correct options highlighted green + solution explanations + score.
- **$N reference resolution**: handles Next.js flight data `$N` references (both decimal AND hex IDs/lengths) — fixes the bug where ~26% of questions had "missing" solutions.

## Quick start

```bash
# Install requirements
pip install playwright
playwright install chromium

# Scrape a single test
python3 repeatermock_scraper.py \
  --test-url "https://repeatermock.com/tb/test-series/ssc-cgl/test/6a2bef33be1bd560ab3a0e66/attempt?lang=en"

# Scrape a whole series (all free tests, 10 parallel workers)
python3 repeatermock_scraper.py \
  --series-url "https://repeatermock.com/tb/test-series/ssc-cgl"

# Scrape multiple series + 20 parallel workers + stop after 50 tests
python3 repeatermock_scraper.py \
  --series-url "https://repeatermock.com/tb/test-series/ssc-cgl" \
                 "https://repeatermock.com/tb/test-series/ssc-reasoning-previous-year-questions" \
  --workers 20 --stop-after 50

# Resume after killing terminal (picks up where it left off)
python3 repeatermock_scraper.py \
  --series-url "https://repeatermock.com/tb/test-series/ssc-cgl" \
  --resume

# Custom output directory
python3 repeatermock_scraper.py --series-url "..." --output-dir ./my-tests
```

## How it works

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Main orchestrator (asyncio)                                  │
│                                                               │
│  1. Discover all tests across all requested series           │
│     (uses 1 discovery worker to walk the API)                │
│                                                               │
│  2. Filter out already-scraped tests (progress.json)         │
│                                                               │
│  3. Spawn N workers (browser contexts) in parallel           │
│     ┌─────────┐ ┌─────────┐ ┌─────────┐                     │
│     │Worker 1 │ │Worker 2 │ │Worker 3 │  ... up to 50       │
│     │         │ │         │ │         │                     │
│     │context  │ │context  │ │context  │                     │
│     │+ init   │ │+ init   │ │+ init   │                     │
│     │script   │ │script   │ │script   │                     │
│     │+ cookies│ │+ cookies│ │+ cookies│                     │
│     └────┬────┘ └────┬────┘ └────┬────┘                     │
│          │           │           │                            │
│          └───────────┴───────────┴── pull from shared queue   │
│                                                               │
│  4. Each worker:                                              │
│     a. grab test from queue                                   │
│     b. POST /start  (refresh cookies on 401)                  │
│     c. POST /submit (refresh cookies on 401)                  │
│     d. GET /solution page → fetch HTML                        │
│     e. parse flight data, resolve $N refs                     │
│     f. render interactive HTML                                │
│     g. save to nested folder, mark done in progress.json      │
│     h. repeat                                                 │
│                                                               │
│  5. Save progress.json after every test (atomic write)        │
└──────────────────────────────────────────────────────────────┘
```

### The $N reference bug fix (why Q1, Q6 had no solutions)

RepeaterMock's solution page is a Next.js app. The page's HTML contains **flight data** — a sequence of `self.__next_f.push([1, "<json>"])` calls that encode the React tree as JSON.

The flight data has a peculiar structure:
- `testData` — all questions + 4 options each + 24-language text
- `answersData` — correct option + written solution per question

**Some solutions are stored as references**, not inline text:
```json
"sol": { "en": { "value": "$32" } }
```

The `$32` means "go look up line 32 in the flight data". Line 32 is a **text node**:
```
32:T596,<p>The arrangement of the given words...</p>
```

The `T596` means "the next 596 characters are the text".

### The four cases my resolver handles

Both the **line ID** AND the **length** can be decimal OR hex:

| Line ID | Length | Example | Meaning |
|---------|--------|---------|---------|
| decimal | decimal | `32:T596,...` | Line 32, 596 chars |
| hex | decimal | `3e:T596,...` | Line 62 (0x3e), 596 chars |
| decimal | hex | `32:T4bb,...` | Line 32, 1211 chars (0x4bb) |
| hex | hex | `3e:T4bb,...` | Line 62, 1211 chars |

My regex `([0-9a-f]+):T([0-9a-f]+),` matches all four. Previous scrapers only matched decimal, so ~26% of solutions (the ones with hex refs) showed up as "missing".

### How the script bypasses RepeaterMock's anti-devtools protection

RepeaterMock has aggressive anti-devtools protection that **self-destructs the page** when it detects automation. Without bypassing this, every scraper fails — the page just shows `about:blank` after a few seconds.

The site's detection vectors:
1. **Console-log getter trap**: `console.log(objectWithGetter)` — when devtools renders the object, the getter fires → site knows devtools is open
2. **`debugger` statement loop**: `setInterval(() => { debugger; }, 1000)` — if devtools is open, the debugger pauses, and the site's timer detects the delay
3. **Self-destruct**: `window.close()`, `console.clear()`, `location.replace('about:blank')`, `document.write('')`

My `INIT_SCRIPT` (injected via Playwright's `add_init_script()` BEFORE any page JS runs) neutralizes every vector:
- Blocks `window.close()`, `console.clear()`, `window.stop()`
- Blocks `location.replace/assign/href-setter` to `about:blank`
- Blocks `window.open` to `about:blank`
- Blocks `history.replaceState/pushState` to `about:blank`
- Blocks `document.write` of short destructive content
- Makes `console.log/table/dir/debug/info/trace/group` no-ops (defeats the getter trap — no object to render = no getter to fire)
- Replaces `debugger` with `void 0` in dynamically-evaluated code (`eval`, `setTimeout`, `setInterval` with string args)

## API flow

### Per test
```
1. POST /api/v1/attempts/{testId}/start
   Body: {}
   Returns: { success: true, data: { attemptId, timeLeft, status: "in_progress" } }

2. POST /api/v1/attempts/{testId}/submit
   Body: { "answers": [], "timeTaken": 1, "language": "en", "interface": "classic" }
   Returns: { success: true, data: { attemptId } }

3. GET /tb/test-series/{slug}/test/{testId}/solution
   Returns: HTML with Next.js flight data containing:
     - testData   (questions + options in 24 languages)
     - answersData (correctOption + sol per question)

4. Parse flight data, resolve $N references, render interactive HTML
```

### Per series (discovery)
```
1. GET /api/v1/test-series/{slug}?variant=tb
   Returns: { data: { details: { id, name, sections: [{ id, name, subsections: [...] }] } } }

2. GET /api/v1/test-series/{seriesId}/section-counts
   Returns: { data: [{ sectionId, subSectionId, cachedTestCount }, ...] }

3. For each (sectionId, subSectionId) pair:
   GET /api/v1/test-series/{seriesId}/sections/{sectionId}/tests?limit=500&subSectionId={subId}
   Returns: { data: [{ id, title, isFree, duration, questionCount, totalMark }, ...] }

4. Filter to isFree=true (PRO tests return 402 Payment Required)
```

## Resume capability

Progress is saved to `progress.json` in the output directory after **every** test (atomic write via temp file + rename, so a crash mid-write doesn't corrupt the file).

```json
{
  "ssc-cgl": {
    "scraped": {
      "6a2bef33be1bd560ab3a0e66": {
        "filepath": "/path/to/Alphabet_or_Word_Test_...html",
        "timestamp": "2026-09-03T04:08:27.850696+00:00"
      }
    },
    "failed": [],
    "last_updated": "2026-09-03T04:08:27.850696+00:00"
  }
}
```

On startup, the script loads this file and skips any test ID that's already in `scraped`. Use `--resume` to explicitly continue a previous run (though it also auto-skips without the flag, for safety).

## Nested folder structure

Tests are saved in a folder hierarchy that mirrors the website:

```
output_dir/
  ssc-cgl/                              ← series slug
    Previous_Year_Paper_Tier_I_/       ← section name
      2025/                             ← subsection name
        SSC_CGL_2025_Shift_1_...html    ← test file
        SSC_CGL_2025_Shift_2_...html
      2024/
        SSC_CGL_2024_Shift_1_...html
    All_SSC_Exams_Basic_PYQs_Practice/
      General_Intelligence_and_Reasoning/
        Alphabet_or_Word_Test_...html
  ssc-reasoning-previous-year-questions/
    ...
```

## Interactive mock-test HTML features

Each test HTML file is a fully interactive single-page app:

### Test mode (before submit)
- **Header**: RepeaterMock brand + test title + live countdown timer (turns yellow at 5 min, red+pulsing at 1 min) + Submit Test button
- **Meta bar**: series, question count, duration, max marks, marking scheme, test ID
- **Section tabs**: switch between sections (e.g. "Test", "Quantitative Aptitude")
- **Question card**: question number, marks (+2/-0.5), question text (HTML with images), 4 options (click to select — highlighted blue), language tabs (English/Hindi/Telugu/Marathi/Bengali/Gujarati/Kannada/Tamil/Odia)
- **Action buttons**: Save & Next → (saves answer + goes to next), Mark for Review & Next (marks + goes to next), Clear Response (deselects)
- **Navigation**: Previous / Next buttons + keyboard shortcuts (arrow keys, j/k, 1-4/a-d for options)
- **Question palette** (right sidebar): grid of question numbers, color-coded:
  - 🟢 Green = Answered
  - 🔴 Red = Not Answered (visited but skipped)
  - 🟣 Purple = Marked for Review
  - 🟣 Purple + green dot = Marked & Answered
  - ⬜ Gray = Not Visited
  - 🔵 Blue outline = Current question
- **Live stats**: Answered / Not Answered / Marked / Not Visited counts
- **Submit modal**: "Are you sure?" with summary (answered / not answered / not visited / total)

### Review mode (after submit)
- **Score card**: your score / max marks, correct/wrong/skipped counts, accuracy %
- **All questions** rendered with:
  - Correct option highlighted green with ✓
  - Your selected option (if wrong) highlighted red with ✗
  - If skipped: gray badge
  - Full written solution in a yellow box
- Buttons: View All Questions & Solutions, Print

### Timer behavior
- Counts down from the test's duration
- At 5 minutes remaining: turns yellow
- At 1 minute remaining: turns red + pulses
- At 0: auto-submits

## Free test series (no login required)

### SSC Exams
| Series | URL | Free tests |
|--------|-----|------------|
| SSC CGL 2026 | https://repeatermock.com/tb/test-series/ssc-cgl | ~10 |
| SSC CHSL 2026 | https://repeatermock.com/tb/test-series/ssc-chsl | ~10 |
| SSC CHSL 2025 | https://repeatermock.com/tb/test-series/ssc-chsl-previous | ~10 |
| SSC CPO 2025 | https://repeatermock.com/tb/test-series/ssc-cpo-previous | ~10 |
| SSC CPO 2026 | https://repeatermock.com/tb/test-series/ssc-cpo | ~5 |
| SSC MTS 2026 | https://repeatermock.com/tb/test-series/ssc-mts | ~10 |
| SSC MTS 2025 | https://repeatermock.com/tb/test-series/ssc-mts-previous | ~10 |
| SSC GD Constable 2026 | https://repeatermock.com/tb/test-series/ssc-gd-constable | ~10 |
| SSC Selection Post | https://repeatermock.com/tb/test-series/ssc-selection-post | ~5 |
| SSC Stenographer | https://repeatermock.com/tb/test-series/ssc-stenographer | ~5 |
| SSC Maths PYP (20k+) | https://repeatermock.com/tb/test-series/ssc-maths-previous-year-questions | ~2155 |
| SSC Reasoning PYP (20k+) | https://repeatermock.com/tb/test-series/ssc-reasoning-previous-year-questions | ~2100 |
| SSC English PYP (20k+) | https://repeatermock.com/tb/test-series/ssc-english-previous-year-questions | ~1967 |
| SSC GK PYP (20k+) | https://repeatermock.com/tb/test-series/ssc-gk-previous-year-questions | ~2163 |
| Ace General Knowledge | https://repeatermock.com/tb/test-series/general-knowledge-ssc-railways-competitive-exams | — |

### RRB / Railway Exams
| Series | URL |
|--------|-----|
| RRB Group D 2025-26 | https://repeatermock.com/tb/test-series/rrb-group-d |
| RRB GK PYP | https://repeatermock.com/tb/test-series/rrb-gk-previous-year-questions |
| RRB General Science PYP | https://repeatermock.com/tb/test-series/rrb-general-science-previous-year-questions |

### Banking Exams
| Series | URL |
|--------|-----|
| SBI PO | https://repeatermock.com/gd/test-series/sbi-po |

**Note**: The "Free tests" column is approximate — the script auto-discovers and scrapes all `isFree=true` tests in each series. PRO (paid) tests return `402 Payment Required` and are skipped.

## CLI reference

```
python3 repeatermock_scraper.py [options]

Required (at least one):
  --test-url <url>      URL of a single test (can be repeated)
  --series-url <url>    URL of a series (can be repeated)

Optional:
  --output-dir <path>   Output directory (default: /home/z/my-project/download/repeatermock_tests)
  --workers <n>         Parallel workers (default: 10, max: 50)
  --stop-after <n>      Stop after N tests total (default: unlimited)
  --resume              Resume from previous run (skip already-scraped tests)
```

## Output files

```
output_dir/
├── progress.json                    ← resume state (auto-saved after every test)
├── ssc-cgl/                         ← series slug
│   ├── Previous_Year_Paper_Tier_I_/ ← section name
│   │   ├── 2025/                    ← subsection name
│   │   │   ├── SSC_CGL_2025_..._6a2bef33be1bd560ab3a0e66.html
│   │   │   └── ...
│   │   └── 2024/
│   │       └── ...
│   └── All_SSC_Exams_Basic_PYQs_Practice/
│       └── General_Intelligence_and_Reasoning/
│           ├── Alphabet_or_Word_Test_...html
│           └── ...
└── ssc-reasoning-previous-year-questions/
    └── ...
```

## Notes

- **Free tests only**: the script filters to `isFree=true`. PRO tests return `402 Payment Required` and are skipped. To scrape PRO tests, you'd need to add a paid account's cookies via `context.add_cookies()`.
- **Anti-devtools bypass**: uses Playwright's `add_init_script()` to inject a defensive init script before any page JS runs.
- **$N reference resolution**: the `build_text_refs()` function parses the entire Next.js flight data and resolves all `$N` references (where N can be decimal OR hex) by finding the corresponding `T<len>,<text>` text nodes (where `<len>` can also be decimal OR hex).
- **24 languages**: each question has 24 language fields. The HTML shows tabs for the 10 most common; others are loaded but hidden.
- **Images**: hosted at `https://cdn.repeatermock.com/tb/{hash}.png` — kept as direct links in the HTML.
- **Rate limiting**: 0.5-second delay between tests per worker to avoid rate-limiting.
- **Atomic progress saves**: `progress.json` is written to a temp file first, then atomically renamed — so a crash mid-write never corrupts the file.

## Troubleshooting

**"Page self-destructs to about:blank"**: The init script didn't apply. Make sure you're using `context.add_init_script(INIT_SCRIPT)` BEFORE the first `page.goto()`. The script does this in `Worker.start()`.

**"API call failed (status 0)"**: The browser is on `about:blank` (no origin) OR the Cloudflare clearance expired. The script auto-refreshes cookies and retries 3 times. If it still fails, the worker will skip that test and mark it as failed in progress.json.

**"402 Payment Required" on /start**: The test is PRO (paid). Either use a free test, or modify the script to use a paid account's cookies (set via `context.add_cookies()` in `Worker.start()`).

**"0 questions parsed"**: The flight data parsing failed. Check that the HTML contains `testData` and `answersData`. If only `testData` is present (no `answersData`), the test wasn't submitted before fetching the solution page — make sure `api_start_attempt()` + `api_submit_attempt()` run first.

**Solutions show as `$32` or `$3e` in the HTML**: The text refs didn't resolve. Check that `build_text_refs()` is finding the `T<len>,<text>` patterns. The regex `([0-9a-f]+):T([0-9a-f]+),` should match both decimal and hex IDs/lengths.

**Resume not working**: Make sure the output directory is the same as the previous run. The script reads `progress.json` from `<output-dir>/progress.json`.
