#!/usr/bin/env python3
"""
filter_to_v5.py — Match final_keywords.csv against keywords_v2.5.jsonl → keywords_v5.jsonl

Takes definitions from v2.5 (GPT-rewritten, all relevant=true) and filters to
only the terms approved in final_keywords.csv.

Usage:
    python filter_to_v5.py
    python filter_to_v5.py --input keywords_v2.5.jsonl \
                            --final final_keywords.csv \
                            --output keywords_v5.jsonl
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  default="keywords_v2.5.jsonl")
    parser.add_argument("--final",  default="final_keywords.csv")
    parser.add_argument("--output", default="keywords_v5.jsonl")
    args = parser.parse_args()

    input_path  = Path(args.input)
    final_path  = Path(args.final)
    output_path = Path(args.output)

    for p in [input_path, final_path]:
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    # Load final_keywords allowlist
    print(f"Loading final keywords from: {final_path}")
    final_terms = {}
    with open(final_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            final_terms[row["term"]] = row
    print(f"  Terms in final list: {len(final_terms)}")

    # Load v2.5 as lookup
    print(f"Loading definitions from: {input_path}")
    v25_lookup = {}
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            v25_lookup[entry["keyword"]] = entry
    print(f"  Entries in v2.5: {len(v25_lookup)}")

    # Match and write v5
    print(f"\nBuilding: {output_path}")
    written   = 0
    no_match  = []

    with open(output_path, "w", encoding="utf-8") as fout:
        for term, csv_row in final_terms.items():
            entry = v25_lookup.get(term)
            if not entry:
                no_match.append(term)
                continue

            output = dict(entry)
            output["relevant"] = True
            output["_review_action"]    = csv_row.get("action", "")
            output["_review_avg_score"] = csv_row.get("avg_score", "")
            output["_review_consensus"] = csv_row.get("tag_consensus", "")
            if "irrelevant_reason" in output:
                del output["irrelevant_reason"]

            fout.write(json.dumps(output, ensure_ascii=False) + "\n")
            written += 1

    print(f"""
============================================================
Done.
  Written to {output_path}: {written}
  Not found in v2.5:        {len(no_match)}
============================================================""")

    if no_match:
        print(f"\nTerms in final_keywords.csv with no v2.5 match ({len(no_match)}):")
        for t in sorted(no_match)[:20]:
            print(f"  - {t}")
        if len(no_match) > 20:
            print(f"  ... and {len(no_match) - 20} more")
    else:
        print("All terms matched successfully.")


if __name__ == "__main__":
    main()
