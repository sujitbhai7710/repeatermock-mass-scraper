#!/usr/bin/env python3
"""Generate a chapter-wise database copy of all AI JSON exports.
Organizes by year/exam/subject for AI analysis.

Structure:
  database/
  ├── 2025/
  │   ├── SSC_CGL/
  │   │   ├── English/
  │   │   │   ├── Synonym/
  │   │   │   │   ├── test1_q1.json
  │   │   │   │   └── ...
  │   │   │   ├── Antonym/
  │   │   │   └── ...
  │   │   ├── Reasoning/
  │   │   ├── Maths/
  │   │   └── GK/
  │   ├── SSC_CPO/
  │   ├── SSC_MTS/
  │   └── ...
  ├── 2024/
  ├── 2023/
  └── 2022/

Each question is saved as a separate JSON file with full data (question + options + solution + concept).
"""
import argparse
import json
import os
import re
import glob
from collections import defaultdict
from datetime import datetime


# Exam name detection from test title (e.g. "PYST 1: SSC CGL 2025 - English" → "SSC_CGL")
EXAM_PATTERNS = [
    (r"ssc cgl", "SSC_CGL"),
    (r"ssc chsl", "SSC_CHSL"),
    (r"ssc cpo", "SSC_CPO"),
    (r"ssc mts", "SSC_MTS"),
    (r"ssc gd", "SSC_GD"),
    (r"ssc selection post", "SSC_Selection_Post"),
    (r"ssc stenographer", "SSC_Stenographer"),
    (r"rrb group d", "RRB_Group_D"),
    (r"rrb ntpc", "RRB_NTPC"),
    (r"sbi po", "SBI_PO"),
    (r"sbi clerk", "SBI_Clerk"),
    (r"ibps po", "IBPS_PO"),
    (r"ibps clerk", "IBPS_Clerk"),
]


def detect_exam(title: str) -> str:
    """Detect exam name from test title. Returns 'Other' if not found."""
    title_lower = title.lower()
    for pattern, exam_name in EXAM_PATTERNS:
        if re.search(pattern, title_lower):
            return exam_name
    return "Other"


def detect_year(title: str) -> str:
    """Extract year from test title. Returns 'Unknown' if not found."""
    # Look for 4-digit years 2020-2030
    years = re.findall(r'\b(202[0-9])\b', title)
    if years:
        return years[0]
    # Also check for "Held On: 12 Sept 2025" format
    m = re.search(r'(?:held on|held in)[:\s]+.*?(\d{4})', title, re.IGNORECASE)
    if m:
        return m.group(1)
    return "Unknown"


def detect_year_from_subsection(subsection: str) -> str:
    """Extract year from subsection name (e.g. '2024' → '2024')."""
    years = re.findall(r'(202[0-9])', subsection)
    if years:
        return years[0]
    return ""


def sanitize(s: str, max_len: int = 60) -> str:
    """Sanitize string for use as folder/file name."""
    s = re.sub(r'[^a-zA-Z0-9\-_]+', '_', s).strip('_')
    return s[:max_len] if len(s) > max_len else s


def build_database(output_dir: str, db_dir: str, years_filter: list = None):
    """Scan all AI JSON exports and build a chapter-wise database.
    
    Database filtering rules (per user requirement):
    - Year filter: only 2021-2025 (not before 2021)
    - Section filter: only specific sections per series (see SERIES_SECTION_FILTERS)
    - Subject filter: all subjects
    
    NOTE: The ai_export/ and html_export/ folders contain ALL scraped tests (all years).
    The database/ folder is a FILTERED COPY for AI analysis — only 2021-2025 + specific sections.
    """
    ai_files = sorted(glob.glob(os.path.join(output_dir, "ai_export", "**", "*.json"), recursive=True))
    print(f"Found {len(ai_files)} AI JSON files to process")
    
    if years_filter is None:
        years_filter = ["2025", "2024", "2023", "2022", "2021"]
    
    stats = {
        "total_questions": 0,
        "by_year": defaultdict(int),
        "by_exam": defaultdict(int),
        "by_subject": defaultdict(int),
        "by_concept": defaultdict(int),
        "skipped_year": 0,
    }
    
    for fpath in ai_files:
        try:
            with open(fpath) as f:
                test = json.load(f)
        except Exception as e:
            print(f"  ⚠️ couldn't parse {fpath}: {e}")
            continue
        
        title = test.get("title", "")
        subject = test.get("subject", "general")
        subsection = test.get("subsection", "")
        
        # Detect year and exam
        year = detect_year(title) or detect_year_from_subsection(subsection)
        exam = detect_exam(title)
        
        # Skip if year not in filter
        if year not in years_filter:
            stats["skipped_year"] += 1
            continue
        
        # Process each question
        for q in test.get("questions", []):
            stats["total_questions"] += 1
            concept = q.get("concept", "Unidentified")
            concept_clean = sanitize(concept, max_len=40) if concept else "Unidentified"
            qid = q.get("qid", f"unknown_{stats['total_questions']}")
            
            # Build path: db_dir/year/exam/subject/concept/qid.json
            dir_path = os.path.join(db_dir, year, exam, subject.capitalize(), concept_clean)
            os.makedirs(dir_path, exist_ok=True)
            
            # Save question as individual JSON file
            q_data = {
                "qid": qid,
                "test_id": test.get("test_id", ""),
                "test_title": title,
                "series_slug": test.get("series_slug", ""),
                "section": test.get("section", ""),
                "subsection": subsection,
                "subject": subject,
                "exam": exam,
                "year": year,
                "concept": concept,
                "confidence": q.get("confidence", ""),
                "question": q.get("question", ""),
                "options": q.get("options", []),
                "correct": q.get("correct", ""),
                "solution": q.get("solution", ""),
                "images": q.get("images", []),
                "solution_images": q.get("solution_images", []),
                "tags": q.get("tags", []),
                "marks_pos": q.get("marks_pos", 0),
                "marks_neg": q.get("marks_neg", 0),
                "type": q.get("type", "mcq"),
            }
            
            file_path = os.path.join(dir_path, f"{qid}.json")
            with open(file_path, "w") as f:
                json.dump(q_data, f, ensure_ascii=False, indent=2)
            
            # Update stats
            stats["by_year"][year] += 1
            stats["by_exam"][exam] += 1
            stats["by_subject"][subject] += 1
            stats["by_concept"][concept] += 1
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Build chapter-wise database from AI JSON exports.")
    parser.add_argument("--output-dir", default="/home/z/my-project/download/repeatermock_tests",
                        help="Directory containing ai_export/ subfolder with scraped JSON files")
    parser.add_argument("--db-dir", default=None,
                        help="Output database directory (default: <output-dir>/database)")
    parser.add_argument("--years", nargs="*", default=["2025", "2024", "2023", "2022", "2021"],
                        help="Years to include (default: 2025 2024 2023 2022 2021)")
    args = parser.parse_args()
    
    db_dir = args.db_dir or os.path.join(args.output_dir, "database")
    os.makedirs(db_dir, exist_ok=True)
    
    print(f"Building chapter-wise database from {args.output_dir}/ai_export/...")
    print(f"Output: {db_dir}")
    print(f"Years: {args.years}")
    print()
    
    stats = build_database(args.output_dir, db_dir, args.years)
    
    # Save database index
    index_path = os.path.join(db_dir, "database_index.json")
    index_data = {
        "generated_at": datetime.now().isoformat(),
        "source_dir": args.output_dir,
        "years_included": args.years,
        "stats": {
            "total_questions": stats["total_questions"],
            "skipped_year": stats["skipped_year"],
            "by_year": dict(stats["by_year"]),
            "by_exam": dict(stats["by_exam"]),
            "by_subject": dict(stats["by_subject"]),
            "by_concept": dict(sorted(stats["by_concept"].items(), key=lambda x: -x[1])[:30]),
        },
    }
    with open(index_path, "w") as f:
        json.dump(index_data, f, indent=2)
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"DATABASE BUILD COMPLETE")
    print(f"{'='*60}")
    print(f"  Total questions:        {stats['total_questions']:,}")
    print(f"  Skipped (wrong year):   {stats['skipped_year']:,}")
    print(f"\n  By Year:")
    for year in sorted(stats["by_year"].keys()):
        print(f"    {year}: {stats['by_year'][year]:,}")
    print(f"\n  By Exam:")
    for exam, count in sorted(stats["by_exam"].items(), key=lambda x: -x[1]):
        print(f"    {exam:25s}: {count:,}")
    print(f"\n  By Subject:")
    for subj, count in sorted(stats["by_subject"].items(), key=lambda x: -x[1]):
        print(f"    {subj:15s}: {count:,}")
    print(f"\n  Top Concepts:")
    for concept, count in sorted(stats["by_concept"].items(), key=lambda x: -x[1])[:15]:
        print(f"    {concept:40s}: {count:,}")
    print(f"\n  Database saved to: {db_dir}")
    print(f"  Index saved to:   {index_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
