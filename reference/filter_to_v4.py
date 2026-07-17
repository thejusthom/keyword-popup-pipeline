#!/usr/bin/env python3
"""
filter_to_v4.py — Build keywords_v4.jsonl from keywords_v3.jsonl using two inclusion lists:

  1. approved_supplementary.csv  — model-comparison approved/supplementary terms (6,721)
  2. fix_definition.csv          — terms flagged for definition fixes; definitions are pulled
                                   from v3; entries with empty definitions are included as
                                   placeholders and will be skipped by the uploader.

Usage:
    python filter_to_v4.py
    python filter_to_v4.py --input keywords_v3.jsonl --approved approved_supplementary.csv \
                            --fix fix_definition.csv --output keywords_v4.jsonl
    python filter_to_v4.py --approved-only   # exclude supplementary, keep only approved + fix
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",         default="keywords_v3.jsonl")
    parser.add_argument("--approved",      default="approved_supplementary.csv")
    parser.add_argument("--fix",           default="fix_definition.csv")
    parser.add_argument("--output",        default="keywords_v4.jsonl")
    parser.add_argument("--approved-only", action="store_true",
                        help="Only include action=approved entries (exclude supplementary)")
    args = parser.parse_args()

    input_path    = Path(args.input)
    approved_path = Path(args.approved)
    fix_path      = Path(args.fix)
    output_path   = Path(args.output)

    for p in [input_path, approved_path, fix_path]:
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    # ── Load approved_supplementary ───────────────────────────────────────────

    print(f"Loading approved terms from: {approved_path}")
    approved_terms = {}
    with open(approved_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            action = row.get("action", "").strip().lower()
            if args.approved_only and action != "approved":
                continue
            if action in ("approved", "supplementary"):
                approved_terms[row["term"]] = row
    print(f"  Loaded: {len(approved_terms)}")

    # ── Load fix_definition ───────────────────────────────────────────────────

    print(f"Loading fix_definition terms from: {fix_path}")
    fix_terms = {}
    with open(fix_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            term = row.get("term", "").strip()
            if term and term not in approved_terms:
                fix_terms[term] = row
    print(f"  Loaded: {len(fix_terms)} (definitions pulled from v3)")

    # ── Filter v3 → v4 ───────────────────────────────────────────────────────

    print(f"\nFiltering: {input_path} → {output_path}")

    written = 0
    excluded = 0
    malformed = 0
    action_counts = {"approved": 0, "supplementary": 0, "fix_definition": 0}

    with open(input_path, encoding="utf-8") as fin, \
         open(output_path, "w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue

            keyword = entry.get("keyword", "")

            if keyword in approved_terms:
                csv_row = approved_terms[keyword]
                action = csv_row.get("action", "").lower()
                entry["_review_action"]    = action
                entry["_review_avg_score"] = csv_row.get("avg_score", "")
                entry["_review_consensus"] = csv_row.get("tag_consensus", "")
                action_counts[action] = action_counts.get(action, 0) + 1

            elif keyword in fix_terms:
                csv_row = fix_terms[keyword]
                entry["relevant"]          = True   # override so uploader can evaluate def
                entry["_review_action"]    = "fix_definition"
                entry["_review_avg_score"] = csv_row.get("avg_score", "")
                entry["_review_consensus"] = "fix_definition"
                action_counts["fix_definition"] += 1

            else:
                excluded += 1
                continue

            fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

            if written % 500 == 0:
                print(f"  Written {written}...")

    # ── Summary ───────────────────────────────────────────────────────────────

    print(f"""
============================================================
Done.
  Output              : {output_path}
  Written             : {written}
    — approved        : {action_counts.get('approved', 0)}
    — supplementary   : {action_counts.get('supplementary', 0)}
    — fix_definition  : {action_counts.get('fix_definition', 0)}
  Excluded (not in either list) : {excluded}
  Malformed lines               : {malformed}
============================================================
""")

    # Verify all terms matched
    matched = set()
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                matched.add(json.loads(line).get("keyword", ""))

    for label, term_set in [("approved_supplementary", set(approved_terms)),
                             ("fix_definition",         set(fix_terms))]:
        unmatched = term_set - matched
        if unmatched:
            print(f"WARNING: {len(unmatched)} {label} terms had no match in v3:")
            for t in sorted(unmatched)[:20]:
                print(f"  - {t}")
            if len(unmatched) > 20:
                print(f"  ... and {len(unmatched) - 20} more")
        else:
            print(f"All {label} terms matched successfully.")


if __name__ == "__main__":
    main()
