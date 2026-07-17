#!/usr/bin/env python3
"""
merge_keywords.py — Stage 6: merge Wikipedia keywords + enriched abbreviations.

Priority: Wikipedia entries win on slug collision (they have richer, article-
grounded definitions). Abbreviation entries only fill in slugs not already
covered by Wikipedia.

Usage:
    python merge_keywords.py
    python merge_keywords.py --wiki ../03_wiki_rewrite/keywords_v3.jsonl \\
                              --abbrevs ../05_abbrev_enrich/keywords_abbrevs_enriched.jsonl \\
                              --output keywords_combined.jsonl

Nothing here is subject-specific — this script works unchanged for any domain.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def make_slug(kw: str) -> str:
    s = kw.lower().replace("(", "").replace(")", "")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def main():
    p = argparse.ArgumentParser(description="Stage 6: merge Wikipedia + abbreviation keyword sets")
    p.add_argument('--wiki', default='../03_wiki_rewrite/keywords_v3.jsonl',
                    help="Wikipedia pipeline output (higher priority on collision)")
    p.add_argument('--abbrevs', default='../05_abbrev_enrich/keywords_abbrevs_enriched.jsonl',
                    help="Abbreviation pipeline output")
    p.add_argument('--output', default='keywords_combined.jsonl')
    args = p.parse_args()

    wiki_path = Path(args.wiki)
    abbrev_path = Path(args.abbrevs)
    for path, label in [(wiki_path, "--wiki"), (abbrev_path, "--abbrevs")]:
        if not path.exists():
            print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    by_slug: dict[str, dict] = {}

    # 1. Load Wikipedia output first (higher priority)
    wiki_count = 0
    for line in wiki_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("relevant", True):
            continue   # irrelevant entries never make it into the combined file
        slug = make_slug(row["keyword"])
        by_slug[slug] = row
        wiki_count += 1

    # 2. Load enriched abbreviations — only add if slug not already covered
    abbrev_added = 0
    abbrev_skipped = 0
    for line in abbrev_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        slug = make_slug(row["keyword"])
        if slug not in by_slug:
            by_slug[slug] = row
            abbrev_added += 1
        else:
            abbrev_skipped += 1

    # 3. Write combined file
    with open(args.output, "w", encoding="utf-8") as f:
        for row in by_slug.values():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wikipedia entries:        {wiki_count}")
    print(f"Abbreviations added:      {abbrev_added}")
    print(f"Abbreviations skipped:    {abbrev_skipped}  (slug already covered by Wikipedia)")
    print(f"Total combined:           {len(by_slug)}")
    print(f"Output:                   {args.output}")
    output_abs = Path(args.output).resolve()
    print()
    print("Next step:")
    print(f"  cd ../07_upload && python upload_to_supabase.py --input {output_abs}")


if __name__ == "__main__":
    main()
