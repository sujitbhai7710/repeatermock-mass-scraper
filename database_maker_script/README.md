# Database Maker Scripts (DISABLED — Not Auto-Run)

These scripts are saved here for **local manual use only**. They are NOT called by the GitHub Actions workflow.

## Why disabled?

The previous workflow ran `build_database.py` and `generate_index.py` in the merge job after every matrix run. This caused:
1. **Duplicate question files** — same qid written to multiple paths across runs (no clean slate)
2. **Wrong subject classification** — 65% of questions tagged as "general" instead of their actual subject
3. **Missing shift/tier/date fields** — info was in test_title but not extracted to separate fields
4. **Repo bloat** — 1.8GB of duplicate database files

## When to use these scripts

Run them **locally** when you want to build a chapter-wise database from already-scraped ai_export/ files. NOT during scraping.

## Files

- `build_database.py` — Builds chapter-wise database (by year/exam/subject/concept) from ai_export/ files
- `generate_index.py` — Generates combined question index (index.json + index.html)
- `validate_with_claude.py` — Validates concept detection accuracy using Claude API (optional)

## How to use locally

```bash
# Clone the repo locally (use --filter=blob:none for speed if repo is large)
git clone --filter=blob:none https://github.com/sujitbhai7710/repeatermock-mass-scraper.git
cd repeatermock-mass-scraper

# Build chapter-wise database (reads from scraped_output/ai_export/)
python3 database_maker_script/build_database.py --output-dir scraped_output

# Generate combined index (reads from scraped_output/ai_export/)
python3 database_maker_script/generate_index.py --output-dir scraped_output
```

## Future improvements (TODO)

These scripts need to be rewritten with:
- ✅ Multi-source subject detection (section/subsection/question-text/tags)
- ✅ Shift/Tier/Date extraction from test_title
- ✅ Proper dedup (clean database dir before rebuild)
- ✅ Only read top-level ai_export/ (not per-job copies)

For now, the raw scraped data in `scraped_output/{series}/ai_export/` is the source of truth.
