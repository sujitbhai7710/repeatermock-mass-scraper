#!/usr/bin/env python3
"""Use Claude API to validate the pattern recognition accuracy.
Scrapes a few tests, then asks Claude to independently categorize the same questions.
Compares our regex-based classifier vs Claude's classification."""
import asyncio
import json
import sys
import os
from collections import Counter
from openai import OpenAI

sys.path.insert(0, '/home/z/my-project/scripts')
from repeatermock_scraper import strip_html, detect_concept, detect_subject

CLAUDE_CLIENT = OpenAI(
    base_url="https://kktoken.cc/v1",
    api_key="sk-fJ3HrhShKjmTMtrJEPTfltPYmxawBlfxvGCNTovvXrWE6QnN",
)

def claude_classify(subject: str, question: str, options: list) -> str:
    """Ask Claude to classify a question. Returns the concept label."""
    opts_text = "\n".join(f"  {o['label']}: {o['text']}" for o in options)
    prompt = f"""You are an SSC CGL exam expert. Classify the following {subject} question into ONE concept category.

Question: {question}

Options:
{opts_text}

Respond with ONLY the concept name (1-4 words). Use these specific labels:
- English: Synonym, Antonym, Idioms, Spelling, OWS, Phrasal Verbs, Voice, Narration, Error Detection, Fill in the Blanks, Reading Comprehension, Cloze Test, Sentence Improvement, Para Jumbles, Grammar
- Reasoning: Series, Analogy, Classification, Coding-Decoding, Blood Relations, Direction Sense, Ranking/Order, Puzzle, Syllogism, Venn Diagram, Mirror/Water Image, Calendar, Clock, Alphabet/Word Test, Arrangement and Pattern, Similarity and Differences, Figure Counting
- Maths: Profit & Loss, Simple Interest, Compound Interest, Percentage, Ratio & Proportion, Average, Time & Work, Time Speed & Distance, Boats & Streams, Simplification, Number System, Geometry, Trigonometry, Mensuration, Algebra, Data Interpretation, Partnership, Ages, Pipes & Cisterns, Permutation & Combination, Probability, Statistics
- GK: Polity, History, Geography, Economics, Biology, Chemistry, Physics, Static GK

Respond with ONLY the label, nothing else."""

    try:
        completion = CLAUDE_CLIENT.chat.completions.create(
            model="claude-opus-5",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"


async def main():
    # Load scraped AI JSON files
    import glob
    files = sorted(glob.glob('/home/z/my-project/download/rm_test_balanced/ai_export/**/*.json', recursive=True))
    print(f"Found {len(files)} AI JSON files")
    
    # Pick a sample of questions from each subject
    samples = []
    for fpath in files:
        with open(fpath) as f:
            data = json.load(f)
        subject = data['subject']
        for q in data['questions']:
            # Re-scrape the question text using the updated strip_html
            # (the file may have been generated with old strip_html)
            en = q.get('question', '')  # already stripped
            opts = q.get('options', [])
            if en and opts:
                samples.append({
                    'subject': subject,
                    'qid': q.get('qid', ''),
                    'question': en,
                    'options': opts,
                    'our_concept': q.get('concept', ''),
                    'our_confidence': q.get('confidence', ''),
                })
    
    # Sample 5 per subject (20 total) for Claude validation
    by_subject = {}
    for s in samples:
        by_subject.setdefault(s['subject'], []).append(s)
    
    test_sample = []
    for subj in ['english', 'reasoning', 'maths', 'gk']:
        if subj in by_subject:
            test_sample.extend(by_subject[subj][:5])
    
    print(f"\nValidating {len(test_sample)} questions with Claude API...")
    print(f"{'='*80}")
    
    matches = 0
    mismatches = 0
    for i, s in enumerate(test_sample):
        # Get our classification (re-compute with updated detect_concept)
        our_concept, our_conf = detect_concept(s['subject'], [], s['question'], s['options'])
        # Get Claude's classification
        claude_concept = claude_classify(s['subject'], s['question'], s['options'])
        
        # Normalize for comparison (case-insensitive, strip extra words)
        our_norm = our_concept.lower().split(':')[0].split(',')[0].strip()
        claude_norm = claude_concept.lower().split(':')[0].split(',')[0].strip()
        
        match = our_norm == claude_norm or our_norm in claude_norm or claude_norm in our_norm
        if match:
            matches += 1
            status = '✅'
        else:
            mismatches += 1
            status = '❌'
        
        print(f"\n[{i+1}/{len(test_sample)}] {status} [{s['subject']}]")
        print(f"  Q: {s['question'][:100]}...")
        print(f"  Ours:   {our_concept} ({our_conf})")
        print(f"  Claude: {claude_concept}")
    
    print(f"\n{'='*80}")
    print(f"VALIDATION RESULTS")
    print(f"{'='*80}")
    print(f"  Total questions validated: {len(test_sample)}")
    print(f"  Matches:                   {matches} ({100*matches/len(test_sample):.0f}%)")
    print(f"  Mismatches:                {mismatches} ({100*mismatches/len(test_sample):.0f}%)")


asyncio.run(main())
