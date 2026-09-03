#!/usr/bin/env python3
"""Generate a combined question index from all scraped AI JSON files.
Produces:
  - index.json  (AI-readable: { question_id: { question, concept, confidence, correct, appears_in: [test_ids], test_count: N } })
  - index.html  (human-readable: searchable table of all questions with test counts)

Usage:
  python3 generate_index.py --output-dir /path/to/scraped_output
"""
import argparse
import glob
import html as html_mod
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone


def normalize_question(text: str) -> str:
    """Normalize question text for comparison (strip formatting, lowercase, collapse whitespace)."""
    if not text:
        return ""
    # Remove markdown formatting markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # bold
    text = re.sub(r'__([^_]+)__', r'\1', text)  # underline
    text = re.sub(r'\*([^*]+)\*', r'\1', text)  # italic
    text = re.sub(r'\[IMAGE: [^\]]+\]', '', text)  # image markers
    # Remove HTML entities
    text = html_mod.unescape(text)
    # Collapse whitespace and lowercase
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def build_index(output_dir: str) -> dict:
    """Scan all AI JSON files and build a combined question index.
    
    Index structure:
    {
      "question_id": {
        "qid": "abc123",
        "question": "Select the most appropriate synonym...",
        "concept": "Vocabulary: Synonyms",
        "confidence": "high",
        "correct": "3",
        "options": [...],
        "subject": "english",
        "appears_in": [
          {"test_id": "...", "title": "...", "series_slug": "..."},
          ...
        ],
        "test_count": 5
      }
    }
    
    Also tracks questions by normalized text (for fuzzy matching across IDs).
    """
    ai_files = sorted(glob.glob(os.path.join(output_dir, "ai_export", "**", "*.json"), recursive=True))
    print(f"Found {len(ai_files)} AI JSON files")
    
    # Primary index: by question ID
    index_by_qid = {}
    # Secondary index: by normalized question text (to catch same question with different IDs)
    index_by_text = defaultdict(list)  # normalized_text -> [qid1, qid2, ...]
    
    total_questions = 0
    duplicate_qids = 0
    
    for fpath in ai_files:
        try:
            with open(fpath) as f:
                test = json.load(f)
        except Exception as e:
            print(f"  ⚠️ couldn't parse {fpath}: {e}")
            continue
        
        test_id = test.get("test_id", "")
        test_title = test.get("title", "")
        series_slug = test.get("series_slug", "")
        subject = test.get("subject", "general")
        
        for q in test.get("questions", []):
            total_questions += 1
            qid = q.get("qid", "")
            if not qid:
                continue
            
            question_text = q.get("question", "")
            normalized = normalize_question(question_text)
            
            test_ref = {
                "test_id": test_id,
                "title": test_title,
                "series_slug": series_slug,
                "subject": subject,
            }
            
            if qid in index_by_qid:
                # Same question ID in multiple tests — add this test to appears_in
                index_by_qid[qid]["appears_in"].append(test_ref)
                index_by_qid[qid]["test_count"] = len(index_by_qid[qid]["appears_in"])
                duplicate_qids += 1
            else:
                # New question
                index_by_qid[qid] = {
                    "qid": qid,
                    "question": question_text,
                    "concept": q.get("concept", "Unidentified"),
                    "confidence": q.get("confidence", "unidentified"),
                    "correct": q.get("correct", ""),
                    "options": q.get("options", []),
                    "subject": subject,
                    "appears_in": [test_ref],
                    "test_count": 1,
                }
            
            # Also track by normalized text (for fuzzy matching)
            if normalized:
                if qid not in [q for q in index_by_text[normalized]]:
                    index_by_text[normalized].append(qid)
    
    # Compute stats
    total_unique_qids = len(index_by_qid)
    questions_in_multiple_tests = sum(1 for q in index_by_qid.values() if q["test_count"] > 1)
    
    # Find questions that appear across DIFFERENT test IDs (same text, different qid)
    cross_id_duplicates = {
        text: qids for text, qids in index_by_text.items() if len(qids) > 1
    }
    
    stats = {
        "total_ai_files": len(ai_files),
        "total_question_instances": total_questions,
        "total_unique_qids": total_unique_qids,
        "questions_in_multiple_tests": questions_in_multiple_tests,
        "cross_id_duplicate_groups": len(cross_id_duplicates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Top concepts
    concept_counter = Counter(q["concept"] for q in index_by_qid.values())
    stats["top_concepts"] = concept_counter.most_common(20)
    
    # Subject distribution
    subject_counter = Counter(q["subject"] for q in index_by_qid.values())
    stats["subject_distribution"] = dict(subject_counter)
    
    # Most-repeated questions (appear in most tests)
    most_repeated = sorted(index_by_qid.values(), key=lambda q: q["test_count"], reverse=True)[:50]
    
    return {
        "stats": stats,
        "questions": list(index_by_qid.values()),
        "cross_id_duplicates": [
            {"normalized_text": text, "qids": qids, "count": len(qids)}
            for text, qids in cross_id_duplicates.items()
        ],
        "most_repeated": most_repeated,
    }


def render_index_html(index_data: dict) -> str:
    """Render the combined question index as a searchable HTML page."""
    stats = index_data["stats"]
    questions = index_data["questions"]
    most_repeated = index_data["most_repeated"]
    
    # Sort questions by test_count (most repeated first)
    questions_sorted = sorted(questions, key=lambda q: q["test_count"], reverse=True)
    
    # Build questions JSON for client-side search
    questions_json = json.dumps(questions_sorted, ensure_ascii=False)
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RepeaterMock — Combined Question Index</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f3f4f6; color: #1f2937; line-height: 1.6; }}
.header {{ background: #fff; padding: 24px; border-bottom: 1px solid #e5e7eb; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.header h1 {{ font-size: 24px; color: #1fbad6; margin-bottom: 8px; }}
.header p {{ color: #4b5563; font-size: 14px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }}
.stat-card {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 16px; min-width: 140px; }}
.stat-value {{ font-size: 24px; font-weight: 700; color: #1f2937; }}
.stat-label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
.toolbar {{ background: #fff; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.04); border: 1px solid #e5e7eb; position: sticky; top: 0; z-index: 10; }}
.search-box {{ flex: 1; min-width: 200px; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 6px; font-size: 14px; }}
.filter-select {{ padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 6px; font-size: 13px; background: #fff; cursor: pointer; }}
.results-count {{ font-size: 13px; color: #6b7280; white-space: nowrap; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
th {{ background: #1fbad6; color: #fff; padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; cursor: pointer; user-select: none; }}
th:hover {{ background: #1999b3; }}
td {{ padding: 12px; border-bottom: 1px solid #f3f4f6; font-size: 13px; vertical-align: top; }}
tr:hover {{ background: #f9fafb; }}
.qid {{ font-family: monospace; font-size: 11px; color: #6b7280; }}
.question-text {{ max-width: 400px; overflow: hidden; text-overflow: ellipsis; }}
.concept-badge {{ background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; display: inline-block; }}
.concept-badge.unidentified {{ background: #fef3c7; color: #92400e; }}
.conf-high {{ color: #10b981; font-size: 11px; }}
.conf-unidentified {{ color: #f59e0b; font-size: 11px; }}
.test-count {{ font-weight: 700; color: #1fbad6; font-size: 16px; text-align: center; }}
.test-count.high {{ color: #ef4444; }}
.subject-badge {{ background: #f3f4f6; color: #4b5563; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.expand-btn {{ background: #f3f4f6; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
.expand-btn:hover {{ background: #e5e7eb; }}
.test-list {{ display: none; margin-top: 8px; padding: 8px; background: #f9fafb; border-radius: 4px; font-size: 12px; }}
.test-list.open {{ display: block; }}
.test-list div {{ margin-bottom: 4px; }}
.footer {{ text-align: center; padding: 24px; color: #6b7280; font-size: 12px; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 Combined Question Index</h1>
  <p>All questions across all scraped tests — with cross-test deduplication by question ID</p>
  <div class="stats">
    <div class="stat-card"><div class="stat-value">{stats['total_unique_qids']:,}</div><div class="stat-label">Unique Questions</div></div>
    <div class="stat-card"><div class="stat-value">{stats['total_question_instances']:,}</div><div class="stat-label">Total Instances</div></div>
    <div class="stat-card"><div class="stat-value">{stats['total_ai_files']:,}</div><div class="stat-label">Tests Scraped</div></div>
    <div class="stat-card"><div class="stat-value">{stats['questions_in_multiple_tests']:,}</div><div class="stat-label">In Multiple Tests</div></div>
    <div class="stat-card"><div class="stat-value">{stats['cross_id_duplicate_groups']:,}</div><div class="stat-label">Cross-ID Duplicates</div></div>
  </div>
</div>
<div class="container">
  <div class="toolbar">
    <input type="text" class="search-box" id="search" placeholder="🔍 Search questions, concepts, IDs..." oninput="filterTable()">
    <select class="filter-select" id="subjectFilter" onchange="filterTable()">
      <option value="">All Subjects</option>
      {''.join(f'<option value="{s}">{s.title()}</option>' for s in sorted(stats.get('subject_distribution', {}).keys()))}
    </select>
    <select class="filter-select" id="sortBy" onchange="sortTable()">
      <option value="test_count">Sort: Most Repeated</option>
      <option value="question">Sort: Question Text</option>
      <option value="concept">Sort: Concept</option>
      <option value="qid">Sort: Question ID</option>
    </select>
    <span class="results-count" id="resultsCount"></span>
  </div>
  <table id="questionsTable">
    <thead>
      <tr>
        <th style="width:40px;">#</th>
        <th style="width:50px;">Count</th>
        <th style="width:120px;">Question ID</th>
        <th>Question</th>
        <th style="width:140px;">Concept</th>
        <th style="width:80px;">Confidence</th>
        <th style="width:80px;">Subject</th>
        <th style="width:60px;">Correct</th>
        <th style="width:80px;">Tests</th>
      </tr>
    </thead>
    <tbody id="tableBody"></tbody>
  </table>
</div>
<div class="footer">
  Generated at {stats['generated_at']}<br>
  RepeaterMock Mass Scraper — Combined Question Index
</div>
<script>
const ALL_QUESTIONS = {questions_json};
let currentSort = 'test_count';

function renderTable(questions) {{
  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = '';
  questions.forEach((q, i) => {{
    const tr = document.createElement('tr');
    const conceptClass = q.confidence === 'unidentified' ? 'concept-badge unidentified' : 'concept-badge';
    const countClass = q.test_count > 3 ? 'test-count high' : 'test-count';
    const testsList = q.appears_in.map(t => `<div>• ${{t.title}} <span style="color:#9ca3af;">(${{t.series_slug}})</span></div>`).join('');
    tr.innerHTML = `
      <td>${{i + 1}}</td>
      <td class="${{countClass}}">${{q.test_count}}</td>
      <td class="qid">${{q.qid}}</td>
      <td class="question-text">${{escapeHtml(q.question).slice(0, 200)}}${{q.question.length > 200 ? '...' : ''}}</td>
      <td><span class="${{conceptClass}}">${{q.concept}}</span></td>
      <td class="conf-${{q.confidence}}">${{q.confidence}}</td>
      <td><span class="subject-badge">${{q.subject}}</span></td>
      <td><strong>${{q.correct || '—'}}</strong></td>
      <td>
        ${{q.test_count > 1 ? `<button class="expand-btn" onclick="toggleTests('${{q.qid}}', this)">▼ Show ${{q.test_count}} tests</button><div class="test-list" id="tests-${{q.qid}}">${{testsList}}</div>` : `<span style="color:#9ca3af;font-size:11px;">1 test</span>`}}
      </td>
    `;
    tbody.appendChild(tr);
  }});
  document.getElementById('resultsCount').textContent = `${{questions.length}} of ${{ALL_QUESTIONS.length}} questions`;
}}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}}

function toggleTests(qid, btn) {{
  const el = document.getElementById('tests-' + qid);
  if (el.classList.contains('open')) {{
    el.classList.remove('open');
    btn.textContent = '▼ Show tests';
  }} else {{
    el.classList.add('open');
    btn.textContent = '▲ Hide tests';
  }}
}}

function filterTable() {{
  const search = document.getElementById('search').value.toLowerCase();
  const subject = document.getElementById('subjectFilter').value;
  const filtered = ALL_QUESTIONS.filter(q => {{
    if (subject && q.subject !== subject) return false;
    if (search) {{
      const text = (q.question + ' ' + q.concept + ' ' + q.qid + ' ' + q.subject).toLowerCase();
      if (!text.includes(search)) return false;
    }}
    return true;
  }});
  sortAndRender(filtered);
}}

function sortTable() {{
  currentSort = document.getElementById('sortBy').value;
  filterTable();
}}

function sortAndRender(questions) {{
  const sorted = [...questions].sort((a, b) => {{
    if (currentSort === 'test_count') return b.test_count - a.test_count;
    if (currentSort === 'question') return (a.question || '').localeCompare(b.question || '');
    if (currentSort === 'concept') return (a.concept || '').localeCompare(b.concept || '');
    if (currentSort === 'qid') return (a.qid || '').localeCompare(b.qid || '');
    return 0;
  }});
  renderTable(sorted);
}}

// Initial render
sortAndRender(ALL_QUESTIONS);
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate combined question index from scraped AI JSON files.")
    parser.add_argument("--output-dir", default="/home/z/my-project/download/repeatermock_tests",
                        help="Output directory containing ai_export/ subfolder")
    args = parser.parse_args()
    
    print(f"Building index from {args.output_dir}/ai_export/...")
    index_data = build_index(args.output_dir)
    
    # Save index.json (AI-readable)
    index_json_path = os.path.join(args.output_dir, "index.json")
    with open(index_json_path, "w") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved index.json ({os.path.getsize(index_json_path):,} bytes)")
    
    # Save index.html (human-readable)
    index_html_path = os.path.join(args.output_dir, "index.html")
    html = render_index_html(index_data)
    with open(index_html_path, "w") as f:
        f.write(html)
    print(f"✅ Saved index.html ({os.path.getsize(index_html_path):,} bytes)")
    
    # Print summary
    stats = index_data["stats"]
    print(f"\n{'='*60}")
    print(f"INDEX SUMMARY")
    print(f"{'='*60}")
    print(f"  Total AI files scanned:        {stats['total_ai_files']}")
    print(f"  Total question instances:      {stats['total_question_instances']}")
    print(f"  Unique question IDs:           {stats['total_unique_qids']}")
    print(f"  Questions in multiple tests:    {stats['questions_in_multiple_tests']}")
    print(f"  Cross-ID duplicate groups:      {stats['cross_id_duplicate_groups']}")
    print(f"\n  Subject distribution:")
    for subj, count in sorted(stats.get("subject_distribution", {}).items()):
        print(f"    {subj:12s}: {count}")
    print(f"\n  Top concepts:")
    for concept, count in stats["top_concepts"][:10]:
        print(f"    {concept:40s}: {count}")
    
    # Show top 5 most-repeated questions
    print(f"\n  Top 5 most-repeated questions:")
    for q in index_data["most_repeated"][:5]:
        print(f"    [count={q['test_count']}] {q['question'][:80]}...")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
