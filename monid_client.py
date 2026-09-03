#!/usr/bin/env python3
"""Monid API client — search the web via TinyFish provider and fetch page content."""
import json
import urllib.request

MONID_API_KEY = "monid_live_rZKO0dISYa9quF7HMnmbm9mM"
MONID_API_BASE = "https://api.monid.ai/v1"

def monid_search(query: str, purpose: str = "", page: int = 0) -> list:
    """Search the web via Monid/TinyFish. Returns list of results."""
    payload = {
        "provider": "tinyfish",
        "endpoint": "/search",
        "input": {
            "queryParams": {
                "query": query,
                "purpose": purpose or "Research for educational content categorization",
                "page": page
            }
        }
    }
    req = urllib.request.Request(
        f"{MONID_API_BASE}/run",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {MONID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        if data.get("status") == "COMPLETED" and "output" in data:
            return data["output"].get("results", [])
        return []
    except Exception as e:
        print(f"  [monid] search error: {e}")
        return []

def monid_fetch(url: str) -> str:
    """Fetch the full text content of a URL via Monid/TinyFish /fetch endpoint."""
    payload = {
        "provider": "tinyfish",
        "endpoint": "/fetch",
        "input": {
            "queryParams": {
                "url": url
            }
        }
    }
    req = urllib.request.Request(
        f"{MONID_API_BASE}/run",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {MONID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        if data.get("status") == "COMPLETED" and "output" in data:
            out = data["output"]
            # The output might be a dict with 'content' or 'text' or 'markdown'
            if isinstance(out, dict):
                return out.get("content", "") or out.get("text", "") or out.get("markdown", "") or json.dumps(out)
            elif isinstance(out, str):
                return out
        return ""
    except Exception as e:
        print(f"  [monid] fetch error: {e}")
        return ""

if __name__ == "__main__":
    # Test: search for SSC CGL English syllabus
    results = monid_search(
        "SSC CGL English syllabus complete topics list",
        "Find complete SSC CGL English syllabus to categorize exam questions"
    )
    print(f"Found {len(results)} results")
    for r in results[:5]:
        print(f"  [{r.get('position')}] {r.get('title','')[:60]}")
        print(f"      {r.get('url','')[:80]}")
        print(f"      {r.get('snippet','')[:150]}")
