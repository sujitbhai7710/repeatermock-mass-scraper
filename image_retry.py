#!/usr/bin/env python3
"""Image Retry Script — re-downloads failed images WITHOUT re-scraping tests.

Reads image_failures.json from each series folder, re-attempts download with
the new bug fixes (URL sanitization, Wikipedia rewrites, Google Referer headers).
Updates the manifest and removes successful retries from failures list.

Usage:
    python3 image_retry.py                                   # retry all series
    python3 image_retry.py --series SSC-CGL-2026             # retry one series
    python3 image_retry.py --dry-run                         # show what would be retried
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, quote


GITHUB_RAW = "https://raw.githubusercontent.com/sujitbhai7710/repeatermock-mass-scraper/main/"
WEB_BASE = "https://repeatermock.com"
SERIES_LIST = [
    "SSC-CGL-2026","SSC-CHSL-2026","SSC-MTS-2026","SSC-GD-2026","SSC-CPO-2026","SSC-Steno-2026","SSC-SelPost-2026",
    "SSC-CHSL-2025","SSC-CPO-2025","SSC-MTS-2025",
    "Maths-PYP","Reasoning-PYP","English-PYP","GK-PYP",
    "RRB-Group-D","RRB-NTPC-UG","RRB-NTPC-Grad","RRB-ALP-2026","RRB-Tech-2026","RRB-Tech-Prev","RRB-ALP-Prev","RRB-Tech-Gr1",
]


def sanitize_url(url: str) -> str:
    """Same fixes as ImageDownloader._sanitize_url() in the scraper."""
    if not url:
        return url
    # Bug #1: Strip control characters
    url = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', url)
    # Re-encode path/query
    try:
        parts = urlsplit(url)
        clean_path = quote(parts.path, safe='/')
        clean_query = quote(parts.query, safe='=&')
        url = urlunsplit((parts.scheme, parts.netloc, clean_path, clean_query, parts.fragment))
    except Exception:
        pass
    # Bug #2: Wikipedia thumbnail rewrite
    if any(h in url.lower() for h in ['wikipedia.org', 'wikimedia.org']) and '/thumb/' in url:
        url = re.sub(r'/\d+px-', '/500px-', url)
    return url


def get_headers(url: str) -> dict:
    """Same headers as ImageDownloader.download_one() in the scraper."""
    is_google = any(h in url for h in ['googleusercontent.com', 'gstatic.com', 'google.com'])
    is_wikipedia = any(h in url for h in ['wikipedia.org', 'wikimedia.org'])
    if is_google:
        return {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            "Referer": "https://repeatermock.com/",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
    elif is_wikipedia:
        return {
            "User-Agent": "RepeaterMockScraper/1.0 (https://github.com/sujitbhai7710/repeatermock-mass-scraper; educational use)",
            "Referer": "https://repeatermock.com/",
            "Accept": "image/*/*",
        }
    return {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Referer": WEB_BASE,
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }


def local_path_for(cdn_url: str, images_dir: str) -> str:
    """Compute local path for a CDN URL (same logic as scraper)."""
    url_hash = hashlib.sha256(cdn_url.encode()).hexdigest()[:32]
    if ".png" in cdn_url.lower(): ext = "png"
    elif ".jpg" in cdn_url.lower() or ".jpeg" in cdn_url.lower(): ext = "jpg"
    elif ".gif" in cdn_url.lower(): ext = "gif"
    elif ".webp" in cdn_url.lower(): ext = "webp"
    elif ".svg" in cdn_url.lower(): ext = "svg"
    else: ext = "png"
    return os.path.join(images_dir, f"{url_hash}.{ext}")


async def retry_one(cdn_url: str, images_dir: str) -> tuple:
    """Retry downloading one image. Returns (success: bool, error_or_path: str)."""
    clean_url = sanitize_url(cdn_url)
    abs_path = local_path_for(cdn_url, images_dir)
    # Skip if already downloaded (idempotent)
    if os.path.exists(abs_path):
        return True, f"images/{os.path.basename(abs_path)}"
    try:
        req = urllib.request.Request(clean_url, headers=get_headers(cdn_url))
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            data = resp.read()
        if len(data) > 95 * 1024 * 1024:
            return False, "Too large"
        if len(data) < 4:
            return False, "Too short"
        if data[:9].lower().startswith(b'<!doctype') or data[:5].lower().startswith(b'<html'):
            return False, "HTML error page"
        tmp = abs_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.rename(tmp, abs_path)
        return True, f"images/{os.path.basename(abs_path)}"
    except Exception as e:
        return False, str(e)[:200]


async def retry_series(series: str, output_dir: str, dry_run: bool = False) -> dict:
    """Retry all failed images for one series."""
    series_dir = os.path.join(output_dir, series)
    failures_path = os.path.join(series_dir, "image_failures.json")
    manifest_path = os.path.join(series_dir, "images_manifest.json")
    images_dir = os.path.join(series_dir, "images")

    if not os.path.exists(failures_path):
        return {"series": series, "skipped": "no failures file"}

    with open(failures_path) as f:
        failures_data = json.load(f)
    failures = failures_data.get("failures", [])
    if not failures:
        return {"series": series, "total": 0, "recovered": 0, "still_failed": 0}

    # Load existing manifest
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f).get("manifest", {})

    print(f"\n=== {series} ===")
    print(f"  Failures to retry: {len(failures)}")

    if dry_run:
        for f in failures[:5]:
            print(f"    - {f['cdn_url'][:80]}...")
            print(f"      Error: {f.get('error', '?')[:60]}")
        return {"series": series, "dry_run": True, "total": len(failures)}

    recovered = 0
    still_failed = []
    sem = asyncio.Semaphore(5)  # 5 parallel downloads

    async def retry_with_sem(failure):
        async with sem:
            success, result = await retry_one(failure["cdn_url"], images_dir)
            return failure, success, result

    tasks = [retry_with_sem(f) for f in failures]
    results = await asyncio.gather(*tasks)

    for failure, success, result in results:
        if success:
            recovered += 1
            manifest[failure["cdn_url"]] = result
        else:
            still_failed.append({
                **failure,
                "error": result,
                "retry_count": failure.get("retry_count", 0) + 1,
                "last_attempt": datetime.now(timezone.utc).isoformat(),
            })

    # Save updated manifest
    with open(manifest_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_images": len(manifest),
            "description": "Maps CDN URLs to local image paths (updated by image_retry.py).",
            "manifest": manifest,
        }, f, indent=2, ensure_ascii=False)

    # Save updated failures (only those still failing)
    with open(failures_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_failures": len(still_failed),
            "description": "Images that still fail to download after retry. Some external sites may have removed the image (404).",
            "failures": still_failed,
        }, f, indent=2, ensure_ascii=False)

    print(f"  ✅ Recovered: {recovered}")
    print(f"  ❌ Still failing: {len(still_failed)}")
    return {"series": series, "total": len(failures), "recovered": recovered, "still_failed": len(still_failed)}


async def main_async(args):
    output_dir = args.output_dir
    if args.series:
        series_list = [args.series]
    else:
        series_list = SERIES_LIST

    print(f"Image Retry Script")
    print(f"Output dir: {output_dir}")
    print(f"Series: {len(series_list)}")
    if args.dry_run:
        print(f"DRY RUN — no changes will be made")

    all_results = []
    for series in series_list:
        result = await retry_series(series, output_dir, dry_run=args.dry_run)
        all_results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print(f"RETRY SUMMARY")
    print(f"{'='*60}")
    total_recovered = sum(r.get("recovered", 0) for r in all_results)
    total_still_failed = sum(r.get("still_failed", 0) for r in all_results)
    print(f"  Total recovered: {total_recovered}")
    print(f"  Total still failing: {total_still_failed}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Re-download failed images without re-scraping tests.")
    parser.add_argument("--output-dir", default="scraped_output",
                        help="Directory containing scraped_output/<series>/ folders")
    parser.add_argument("--series", default=None,
                        help="Retry only this series (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be retried without making changes")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
