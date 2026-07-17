"""
merge_keywords.py
-----------------
Merges keywords_v5.jsonl + keywords_abbrevs_enriched.jsonl into
keywords_combined.jsonl, deduplicating by slug.

Priority: v5 entries win on collision (they have richer Wikipedia-sourced
definitions). Abbreviation entries only fill in slugs not already in v5.

Run:
    python3 merge_keywords.py

Output: keywords_combined.jsonl  (ready for upload_to_supabase.py)
"""

import json, re
from pathlib import Path

V5_FILE      = "keywords_v5.jsonl"
ABBREV_FILE  = "keywords_abbrevs_enriched.jsonl"
OUTPUT_FILE  = "keywords_combined.jsonl"

def make_slug(kw: str) -> str:
    s = kw.lower().replace("(","").replace(")","")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")

by_slug: dict[str, dict] = {}

# 1. Load v5 first (higher priority)
v5_count = 0
for line in Path(V5_FILE).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    slug = make_slug(row["keyword"])
    by_slug[slug] = row
    v5_count += 1

# 2. Load enriched abbreviations — only add if slug not already in v5
abbrev_added = 0
abbrev_skipped = 0
for line in Path(ABBREV_FILE).read_text(encoding="utf-8").splitlines():
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
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    for row in by_slug.values():
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

print(f"v5 entries:               {v5_count}")
print(f"Abbreviations added:      {abbrev_added}")
print(f"Abbreviations skipped:    {abbrev_skipped}  (slug already in v5)")
print(f"Total combined:           {len(by_slug)}")
print(f"Output:                   {OUTPUT_FILE}")
print()
print("Next step:")
print('  python3 upload_to_supabase.py --input keywords_combined.jsonl --output ./data/keywords.json')
