#!/usr/bin/env python3
"""
sample_for_review.py — Pull a stratified random sample from Supabase
for manual keyword quality review.

Fetches slug + term + category + definition from the articles table,
samples proportionally across categories, and writes a CSV ready
for a human reviewer to fill in.

Target: 90 keywords → 99% confidence that true error rate ≤ 5%
        59 keywords → 95% confidence that true error rate ≤ 5%

Usage:
    python sample_for_review.py            # default 90 samples
    python sample_for_review.py --n 59
    python sample_for_review.py --n 90 --output review_batch1.csv
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_dotenv():
    for p in [Path(__file__).parent / ".env", Path.cwd() / ".env"]:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TABLE        = "articles"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch(url: str) -> list[dict]:
    """Fetch all pages from a PostgREST endpoint (handles 1000-row limit)."""
    results = []
    offset  = 0
    limit   = 1000
    while True:
        paged = f"{url}&limit={limit}&offset={offset}"
        req   = urllib.request.Request(paged, headers={**HEADERS, "Prefer": "count=none"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read())
                results.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            sys.exit(1)
    return results

# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------

def stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """
    Sample n rows proportionally across categories.
    Any remainder is distributed to the largest categories.
    """
    rng = random.Random(seed)

    # Group by category
    groups: dict[str, list[dict]] = {}
    for row in rows:
        cat = row.get("category") or "Unknown"
        groups.setdefault(cat, []).append(row)

    total = len(rows)
    # Calculate quota per category (proportional)
    quotas: dict[str, int] = {}
    for cat, members in groups.items():
        quotas[cat] = max(1, math.floor(n * len(members) / total))

    # Distribute remainder to largest categories
    allocated = sum(quotas.values())
    remainder = n - allocated
    if remainder > 0:
        by_size = sorted(groups.keys(), key=lambda c: len(groups[c]), reverse=True)
        for cat in by_size[:remainder]:
            quotas[cat] += 1

    sample = []
    for cat, quota in quotas.items():
        members = groups[cat]
        take    = min(quota, len(members))
        sample.extend(rng.sample(members, take))

    # If we're still short (small categories), top up from remaining rows
    if len(sample) < n:
        sampled_slugs = {r["slug"] for r in sample}
        remaining = [r for r in rows if r["slug"] not in sampled_slugs]
        extra = min(n - len(sample), len(remaining))
        sample.extend(rng.sample(remaining, extra))

    rng.shuffle(sample)
    return sample

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Pull stratified review sample from Supabase.")
    p.add_argument("--n",      type=int, default=90,
                   help="Number of keywords to sample (default: 90)")
    p.add_argument("--seed",   type=int, default=42,
                   help="Random seed for reproducibility (default: 42)")
    p.add_argument("--output", default="review_sample.csv",
                   help="Output CSV filename (default: review_sample.csv)")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"Fetching all entries from Supabase ({TABLE})…")
    url  = f"{SUPABASE_URL}/rest/v1/{TABLE}?select=slug,term,category,definition"
    rows = fetch(url)
    print(f"  {len(rows)} entries fetched.")

    if len(rows) < args.n:
        print(f"ERROR: Only {len(rows)} rows in DB, can't sample {args.n}.", file=sys.stderr)
        sys.exit(1)

    sample = stratified_sample(rows, args.n, args.seed)
    print(f"  Sampled {len(sample)} entries (stratified by category, seed={args.seed}).")

    # Category breakdown
    from collections import Counter
    cats = Counter(r.get("category","?") for r in sample)
    print("\n  Category breakdown:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

    # Write CSV
    out_path = Path(__file__).parent / args.output
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "slug", "term", "category", "definition",
            "correct (Y/N)", "notes"
        ])
        for row in sample:
            writer.writerow([
                row.get("slug", ""),
                row.get("term", ""),
                row.get("category", ""),
                row.get("definition", ""),
                "",   # reviewer fills: Y or N
                "",   # reviewer fills: notes on errors
            ])

    print(f"\nReview CSV written → {out_path}")
    print(f"\nStatistical guarantee:")
    print(f"  Sample size : {len(sample)}")
    print(f"  If 0 errors : 99% confident true error rate ≤ 5% (≥95% accurate)")
    print(f"  If 0 errors : 95% confident true error rate ≤ 3.3% (≥96.7% accurate)")
    print(f"  If errors found: re-run after fixes with --seed <different_number>")


if __name__ == "__main__":
    main()
