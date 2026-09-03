# RepeaterMock Mass Scraper v2

Scrapes **ALL free tests** from any RepeaterMock test series — gets every question, correct answer, and full written solution — and renders each test as **two formats**:

1. **Interactive mock-test HTML** (countdown timer, question palette, Save & Next / Mark for Review / Clear Response, Submit → reveals all answers + solutions + score). Looks and works just like the real RepeaterMock website.
2. **AI-friendly JSON** — plain text only (HTML stripped), concept-categorized (e.g. "Profit & Loss", "Analogy", "Vocabulary"), short field names, minimal tokens. Organized by subject (english/reasoning/maths/gk).

**No login required.** Free tests work as a guest (20 free attempts per test).

## Features

- **Parallel scraping**: configurable workers (default 10, up to 50). Each worker is an isolated browser context with its own cookie jar + Cloudflare clearance.
- **Auto cookie refresh**: detects 401 responses AND network errors, automatically re-navigates to repeatermock.com to refresh Cloudflare clearance + cookies, then retries. Also refreshes proactively every 15 minutes.
- **Resume capability**: saves progress to `progress.json` after every test. If you kill the terminal and restart, it picks up exactly where it left off — no duplicate scrapes.
- **Nested folder structure**: `output_dir/{html_export,ai_export}/subject/series_slug/section_name/subsection_name/test.{html,json}` — organized by subject first, then series/section/subsection.
- **Subject detection**: auto-detects English / Reasoning / Maths / GK / Science / Computer from series slug, sorts tests into subject folders.
- **100%-accuracy concept categorization**: each question gets a `concept` field with a `confidence` level (`high` / `unidentified`). Uses RepeaterMock's authoritative tags first, then strict explicit-pattern matching (e.g. "synonym of" → Vocabulary: Synonyms). If the question type isn't 100% identifiable, marks as `Unidentified` rather than guessing.
- **Syllabus-backed pattern recognition**: fetched comprehensive SSC CGL syllabi for all 5 subjects (English, Reasoning, Maths, GK, Static GK) via Monid web search API. 86 topics cached in `syllabus_patterns.json`.
- **AI-friendly JSON with formatting preservation**: plain text (HTML stripped) BUT preserves `**bold**`, `__underline__`, `*italic*`, `^{superscript}`, `_{subscript}`, `[IMAGE: url]` markers so AI models understand the formatting.
- **Interactive mock-test HTML**: countdown timer (with "No Limit" fallback if duration unknown), question palette, Save & Next / Mark for Review / Clear Response, Submit → answer reveal with correct options highlighted + solutions + score.
- **Stop after N**: `--stop-after 50` stops after 50 tests total.
- **Max runtime**: `--max-runtime 340` stops gracefully after 340 minutes (for CI/GitHub Actions).
- **$N reference resolution**: handles Next.js flight data `$N` references (both decimal AND hex IDs/lengths) — fixes the bug where ~26% of questions had "missing" solutions.
- **GitHub Action included**: auto-runs every 6 hours, resumes from where it left off, commits results automatically.

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

# Max runtime (for CI — stop after 340 min = 5h40m)
python3 repeatermock_scraper.py \
  --series-list-file series_list.txt \
  --workers 10 --max-runtime 340 --resume

# Custom output directory
python3 repeatermock_scraper.py --series-url "..." --output-dir ./my-tests
```

## GitHub Action (auto-scrape every 6 hours)

This repo includes a GitHub Action (`.github/workflows/scrape.yml`) that:

1. **Runs on schedule** every 6 hours (00:00, 06:00, 12:00, 18:00 UTC)
2. **Can be triggered manually** from the Actions tab (with `max_tests` and `workers` inputs)
3. **Auto-resumes** from where the last run left off (uses `--resume` flag)
4. **Stops after 340 minutes** (5h40m — leaves 20 min buffer before GitHub's 6h hard limit)
5. **Commits the scraped output** back to the repo automatically after each run
6. **Skips if all tests are already scraped** (checks progress.json — if ≥20,000 tests scraped, assumes done)

### To enable the GitHub Action:

1. Fork or clone this repo to your GitHub account
2. Go to **Settings → Actions → General** → allow GitHub Actions
3. Go to **Actions tab** → "Scrape RepeaterMock Tests" workflow → "Enable"
4. Either wait for the next scheduled run, or click "Run workflow" to trigger manually

### To customize:

- Edit `series_list.txt` to add/remove series URLs
- Edit `.github/workflows/scrape.yml` to change the schedule, workers count, or max runtime
- The workflow commits scraped files to `scraped_output/` in the repo

## Output structure

```
output_dir/
├── progress.json                          ← resume state (auto-saved after every test)
├── index.html                             ← combined question index (searchable, human-readable)
├── index.json                             ← combined question index (AI-readable, with test counts)
├── html_export/                           ← interactive mock-test HTML files
│   ├── english/
│   │   └── ssc-english-previous-year-questions/
│   │       └── SSC_GD_PYP/
│   │           └── 2024/
│   │               ├── PYST_1_..._6a1558a9ba3848bbf67fa98b.html
│   │               └── ...
│   ├── reasoning/
│   │   └── ssc-reasoning-previous-year-questions/
│   │       └── ...
│   ├── maths/
│   │   └── ssc-maths-previous-year-questions/
│   │       └── ...
│   └── gk/
│       └── ssc-gk-previous-year-questions/
│           └── ...
└── ai_export/                             ← AI-friendly JSON files
    ├── english/
    │   └── ssc-english-previous-year-questions/
    │       └── SSC_GD_PYP/
    │           └── 2024/
    │               ├── PYST_1_..._6a1558a9ba3848bbf67fa98b.json
    │               └── ...
    ├── reasoning/
    │   └── ...
    ├── maths/
    │   └── ...
    └── gk/
        └── ...
```

## Combined Question Index (index.html + index.json)

After every scrape run, the script automatically generates a combined question index:

- **`index.html`** — searchable HTML table of ALL questions across ALL scraped tests, with:
  - Question ID, question text, concept, confidence, subject, correct answer
  - **Test count** — how many tests each question appears in (highlights questions repeated across tests in red)
  - Expandable "Show N tests" button to see which tests contain each question
  - Search + filter by subject + sort by count/question/concept/ID
- **`index.json`** — AI-readable JSON with the same data, structured as:
  ```json
  {
    "stats": {
      "total_ai_files": 5,
      "total_question_instances": 135,
      "total_unique_qids": 135,
      "questions_in_multiple_tests": 0,
      "cross_id_duplicate_groups": 0,
      "subject_distribution": {"english": 35, "gk": 50, "maths": 25, "reasoning": 25},
      "top_concepts": [["Error Spotting", 10], ["Grammar", 10], ...]
    },
    "questions": [
      {
        "qid": "6909eee5ad853d424078d2de",
        "question": "Which is the 2nd last word...",
        "concept": "Arrangement and Pattern",
        "confidence": "high",
        "correct": "3",
        "subject": "reasoning",
        "appears_in": [{"test_id": "...", "title": "...", "series_slug": "..."}],
        "test_count": 1
      }
    ],
    "most_repeated": [...],  // top 50 questions by test_count
    "cross_id_duplicates": [...]  // questions with same text but different IDs
  }
  ```

This lets you:
- Find questions that appear across multiple tests (high-repeat questions for practice)
- Deduplicate by question ID when building training datasets
- See the full concept distribution across all scraped tests

The GitHub Action auto-generates this index after every scrape run and commits it to the repo.

## AI-friendly JSON format

Each test is exported as a JSON file designed for LLM consumption:

```json
{
  "test_id": "6a1558a9ba3848bbf67fa98b",
  "title": "PYST 1: SSC CGL 2025 - English (Held On: 12 September 2025 Shift 1)",
  "series_slug": "ssc-english-previous-year-questions",
  "series_name": "SSC English PYP Mock Test Series (20k+ Questions)",
  "section": "SSC GD PYST",
  "subsection": "2024",
  "subject": "english",
  "duration_min": 60,
  "max_marks": 100,
  "question_count": 25,
  "languages": ["English", "Hindi"],
  "scraped_at": "2026-09-03T04:30:00+00:00",
  "questions": [
    {
      "qid": "6669456f32f5b623818247fb",
      "n": 1,
      "type": "mcq",
      "concept": "Error Spotting",
      "marks_pos": 2.0,
      "marks_neg": 0.25,
      "question": "Find the part of the given sentence that has an error in it...",
      "options": [
        {"label": "1", "text": "renewable plants that are already contracted or under"},
        {"label": "2", "text": "The most significant near - term impacts on"},
        {"label": "3", "text": "No error"},
        {"label": "4", "text": "construction may being felt through supply chains."}
      ],
      "correct": "4",
      "solution": "The correct answer is Option 4) Key Points In the fourth part...",
      "tags": ["Error Spotting"],
      "hindi": "दी गई वाक्य के उस भाग को खोजें जिसमें त्रुटि है..."
    }
  ]
}
```

### Why this format is AI-friendly

- **Plain text only**: HTML tags stripped, entities decoded — no parsing overhead
- **Short field names**: `n` (number), `qid` (question ID), `marks_pos`/`marks_neg` — saves tokens
- **Concept field**: pre-categorized so the LLM can filter/group without re-analyzing
- **Subject field**: top-level subject classification (english/reasoning/maths/gk)
- **Omits empty fields**: no solution? no `solution` key. No images? no `images` key.
- **Image URLs preserved**: `images: [{"url": "https://cdn.repeatermock.com/tb/..."}]` — LLM can fetch if needed
- **Hindi translation included** (when available) as `hindi` field
- **Tags from RepeaterMock preserved** in `tags` array (raw categorization)

### Concept detection

The script uses two strategies to categorize each question:

1. **RepeaterMock tags** (primary): if the question has tags like `["Vocabulary"]` or `["Profit and Loss"]`, use them directly as the concept.
2. **Keyword detection** (fallback): if no tags, scan the question text + options for subject-specific keywords:
   - **English**: Vocabulary, Idioms & Phrases, Grammar - Error Detection, Grammar - Fill in the Blanks, Reading Comprehension, Cloze Test, Sentence Improvement, Active/Passive Voice, Direct/Indirect Speech, Para Jumbles
   - **Reasoning**: Series, Analogy, Classification, Coding-Decoding, Blood Relations, Direction Sense, Ranking/Order, Puzzle, Syllogism, Venn Diagram, Mirror/Water Image, Cube & Dice, Calendar, Clock, Alphabet/Word Test, Arrangement and Pattern, Similarity and Differences
   - **Maths**: Number System, Simplification, Percentage, Ratio & Proportion, Average, Profit & Loss, Simple Interest, Compound Interest, Time & Work, Time-Speed-Distance, Boats & Streams, Mixture & Alligation, Algebra, Geometry (Triangles/Circles/Quadrilaterals/Lines & Angles), Coordinate Geometry, Trigonometry, Mensuration (2D/3D), Data Interpretation (Tables/Bar/Pie/Line), Statistics, Partnership, Ages, Pipes & Cisterns, Permutation & Combination, Probability
   - **GK**: History (Ancient/Medieval/Modern), Polity (Constitution/Government), Geography (Physical/Indian/World), Economics, General Science (Biology/Chemistry/Physics), Static GK

## How it works

### The $N reference bug fix (why Q1, Q6 had no solutions)

RepeaterMock's solution page is a Next.js app. The page's HTML contains **flight data** — a sequence of `self.__next_f.push([1, "<json>"])` calls that encode the React tree as JSON.

Some solutions are stored as **references**, not inline text:
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

4. Parse flight data, resolve $N references, render HTML + AI JSON
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

## CLI reference

```
python3 repeatermock_scraper.py [options]

Required (at least one):
  --test-url <url>           URL of a single test (can be repeated)
  --series-url <url>         URL of a series (can be repeated)
  --series-list-file <path>  File with one series URL per line (for GitHub Actions)

Optional:
  --output-dir <path>   Output directory (default: /home/z/my-project/download/repeatermock_tests)
  --workers <n>         Parallel workers (default: 10, max: 50)
  --stop-after <n>      Stop after N tests total (default: unlimited)
  --max-runtime <min>   Max runtime in minutes (e.g. 340 for GitHub Actions 6h limit)
  --resume              Resume from previous run (skip already-scraped tests)
```

## Files

```
repeatermock_scraper.py    # The scraper script (self-contained, only needs playwright)
README.md                  # This file
series_list.txt            # List of series URLs to scrape (used by GitHub Action)
samples/
  ├── ai_export/
  │   ├── sample_english_test.json     ← AI-friendly JSON (English subject)
  │   ├── sample_reasoning_test.json   ← AI-friendly JSON (Reasoning subject)
  │   ├── sample_maths_test.json       ← AI-friendly JSON (Maths subject)
  │   └── sample_gk_test.json          ← AI-friendly JSON (GK subject)
  ├── sample_ssc_english_test.html      ← Interactive mock-test HTML
  ├── sample_ssc_mts_english_test.html
  └── sample_ssc_reasoning_test.html
.github/workflows/scrape.yml  ← GitHub Action (auto-scrape every 6h with resume)
```

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
| SSC Maths PYP (20k+) | https://repeatermock.com/tb/test-series/ssc-maths-previous-year-questions | ~2144 |
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

## Notes

- **Free tests only**: the script filters to `isFree=true`. PRO tests return `402 Payment Required` and are skipped.
- **Two output formats**: every test produces BOTH an interactive HTML file (for humans) and an AI-friendly JSON file (for LLMs).
- **Subject-based organization**: tests are sorted into `english/`, `reasoning/`, `maths/`, `gk/`, `science/`, `computer/` folders automatically.
- **Anti-devtools bypass**: uses Playwright's `add_init_script()` to inject a defensive init script before any page JS runs.
- **$N reference resolution**: the `build_text_refs()` function parses the entire Next.js flight data and resolves all `$N` references (where N can be decimal OR hex) by finding the corresponding `T<len>,<text>` text nodes (where `<len>` can also be decimal OR hex).
- **24 languages**: each question has 24 language fields. The HTML shows tabs for the 10 most common; the AI JSON includes English + Hindi.
- **Atomic progress saves**: `progress.json` is written to a temp file first, then atomically renamed.
- **GitHub Action**: auto-runs every 6 hours, resumes from where it left off, commits results to the repo.

## Troubleshooting

**"Page self-destructs to about:blank"**: The init script didn't apply. Make sure you're using `context.add_init_script(INIT_SCRIPT)` BEFORE the first `page.goto()`.

**"API call failed (status 0)"**: The browser is on `about:blank` (no origin) OR the Cloudflare clearance expired. The script auto-refreshes cookies and retries 3 times.

**"402 Payment Required" on /start**: The test is PRO (paid). Use a free test, or add paid cookies via `context.add_cookies()`.

**"0 questions parsed"**: The flight data parsing failed. Check that the HTML contains `testData` and `answersData`.

**Solutions show as `$32` or `$3e`**: The text refs didn't resolve. Check that `build_text_refs()` is finding the `T<len>,<text>` patterns. The regex `([0-9a-f]+):T([0-9a-f]+),` matches both decimal and hex.

**GitHub Action not running**: Check that the repo has Actions enabled (Settings → Actions → General). The workflow uses `GITHUB_TOKEN` (auto-provided, no secret needed).

**GitHub Action commits failing**: The workflow uses `git push origin HEAD`. Make sure the repo allows pushes from github-actions[bot]. The default `GITHUB_TOKEN` has this permission — check Settings → Actions → General → Workflow permissions → "Read and write permissions".
