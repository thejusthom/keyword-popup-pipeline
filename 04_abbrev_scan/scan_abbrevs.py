#!/usr/bin/env python3
"""
scan_abbrevs.py — Stage 4: scan the textbook's MDX files for abbreviations.

Finds every 2+-uppercase-letter abbreviation used in the textbook, skips
known noise, checks which ones are already covered by Stage 3's Wikipedia
output, and writes placeholder entries for the ones that are missing —
Stage 5 (enrich_abbrevs.py) replaces those placeholders with real,
LLM-written definitions.

Usage:
    python scan_abbrevs.py
    python scan_abbrevs.py --docs ../docs --wiki-output ../03_wiki_rewrite/keywords_v3.jsonl

All subject-specific values (the noise SKIP set, category-inference rules,
optional hand-curated abbreviation definitions) live in ../config.py.
"""

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


def make_slug(kw: str) -> str:
    s = kw.lower().replace("(", "").replace(")", "")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def load_hand_curated_defs() -> list:
    """Optional: load (keyword, full_name, category, wiki_title) tuples from
    a module named in config.ABBREV_DEFS_MODULE. Returns [] if not configured
    — hand-curating abbreviation definitions is optional, not required."""
    if not config.ABBREV_DEFS_MODULE:
        return []
    try:
        mod = importlib.import_module(config.ABBREV_DEFS_MODULE)
        return getattr(mod, "ABBREVS", [])
    except ImportError:
        print(f"WARNING: config.ABBREV_DEFS_MODULE={config.ABBREV_DEFS_MODULE!r} "
              f"not found — continuing with zero hand-curated definitions.", file=sys.stderr)
        return []


def infer_category(abbr: str) -> str:
    """Guess a category for an abbreviation from its own text, using the
    keyword-pattern rules in config.ABBREV_CATEGORY_RULES."""
    a = abbr.upper()
    for category, patterns in config.ABBREV_CATEGORY_RULES:
        if any(p in a for p in patterns):
            return category
    return config.DEFAULT_ABBREV_CATEGORY


def make_stub(abbr: str, count: int) -> tuple:
    """Placeholder for an abbreviation with no hand-curated definition.
    Stage 5 overwrites this with a real LLM-written definition."""
    cat = infer_category(abbr)
    definition = (
        f"{abbr} is a specialized abbreviation used in {cat.lower()} "
        f"and {config.DOMAIN_NAME} literature (appears {count} time{'s' if count > 1 else ''} "
        f"in this textbook). Its exact expansion depends on context; "
        f"see the surrounding chapter text for details."
    )
    return (abbr, abbr, cat, definition, "")


def scan_mdx_for_abbreviations(docs_root: Path) -> dict:
    """Return {abbreviation: frequency_count} across every .mdx/.md file."""
    freq: dict[str, int] = {}
    files = list(docs_root.rglob("*.mdx")) + list(docs_root.rglob("*.md"))
    for mdx in files:
        text = re.sub(r"<[^>]+>", " ", mdx.read_text(errors="ignore"))
        text = re.sub(r"`[^`]*`", " ", text)
        for m in re.finditer(r"\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b", text):
            w = m.group(1)
            freq[w] = freq.get(w, 0) + 1
    return freq, len(files)


def load_cached_scan(cache_path: Path) -> tuple[dict, int] | None:
    """
    Reuse the textbook scan Stage 0 (00_config_generator/generate_config.py)
    already did, instead of re-walking every .mdx file a second time.

    Returns (freq, file_count) if the cache file exists and has the expected
    shape, else None (caller falls back to a fresh scan).
    """
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding='utf-8'))
        freq = data.get("abbrev_freq")
        file_count = data.get("file_count")
        if freq is None or file_count is None:
            return None
        return freq, file_count
    except (json.JSONDecodeError, OSError):
        return None


def main():
    p = argparse.ArgumentParser(description="Stage 4: scan MDX textbook for abbreviations")
    p.add_argument('--docs', default=config.DOCS_ROOT, help='Textbook MDX/MD source directory')
    p.add_argument('--wiki-output', default='../03_wiki_rewrite/keywords_v3.jsonl',
                    help="Stage 3's output — abbreviations already covered here are skipped")
    p.add_argument('--output', default='keywords_abbrevs.jsonl')
    p.add_argument('--report', default='scan_report.csv',
                    help='Per-abbreviation CSV audit trail: abbr,count,status')
    p.add_argument('--cache', default='../00_config_generator/textbook_scan_cache.json',
                    help="Reuse Stage 0's textbook scan instead of re-walking the MDX files. "
                         "If this file doesn't exist, falls back to a fresh scan automatically.")
    p.add_argument('--no-cache', action='store_true',
                    help='Force a fresh scan even if a Stage 0 cache file is present')
    args = p.parse_args()

    docs_root = Path(args.docs)
    if not docs_root.exists():
        print(f"ERROR: docs directory not found: {docs_root}", file=sys.stderr)
        sys.exit(1)

    # ── load Wikipedia output slugs (already-covered set) ──────────────────
    wiki_slugs = set()
    wiki_path = Path(args.wiki_output)
    if wiki_path.exists():
        with open(wiki_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("relevant", True):
                    wiki_slugs.add(make_slug(row["keyword"]))
    else:
        print(f"WARNING: {wiki_path} not found — proceeding as if Wikipedia "
              f"output is empty (every abbreviation will be treated as missing).", file=sys.stderr)

    # ── scan MDX for every abbreviation (reuse Stage 0's cache if available) ──
    cached = None if args.no_cache else load_cached_scan(Path(args.cache))
    if cached is not None:
        freq, file_count = cached
        print(f"Reused Stage 0's textbook scan from {args.cache} ({file_count} files) — skipped re-scanning MDX")
    else:
        freq, file_count = scan_mdx_for_abbreviations(docs_root)
        print(f"Scanned {file_count} files under {docs_root}")
    print(f"Found {len(freq)} unique candidate abbreviations")

    # ── hand-curated definitions (optional) ─────────────────────────────────
    hand_curated = load_hand_curated_defs()
    def_map: dict[str, tuple] = {}
    for entry in hand_curated:
        slug = make_slug(entry[0])
        if slug not in def_map:
            def_map[slug] = entry
    print(f"Loaded {len(def_map)} hand-curated definitions "
          f"({'none configured — skipping straight to Stage 5 enrichment' if not def_map else 'from config.ABBREV_DEFS_MODULE'})")

    # ── build output rows ────────────────────────────────────────────────────
    seen_slugs = set(wiki_slugs)
    output_rows = []
    report_rows = []   # (abbr, count, status)

    def emit(entry_tuple, count=0, is_hand_curated=False):
        keyword, full_name, category, definition_raw, wiki_title = entry_tuple
        slug = make_slug(keyword)
        if slug in seen_slugs:
            return
        seen_slugs.add(slug)

        sentences = re.split(r'(?<=[.!?])\s+', definition_raw.strip())
        short, char_count = "", 0
        for s in sentences[:3]:
            cand = (short + " " + s).strip()
            if len(cand) <= 280:
                short = cand
            else:
                break
        if not short and sentences:
            short = sentences[0][:280]

        wiki_url = f"https://en.wikipedia.org/wiki/{wiki_title}" if wiki_title else ""

        output_rows.append({
            "keyword": keyword,
            "source_url": wiki_url,
            "images": [],
            "categories": [full_name] if full_name != keyword else [],
            "all_page_categories": [full_name, category] if full_name != keyword else [category],
            "relevant": True,
            "category": category,
            "definition_short": short,
            "definition_raw": definition_raw,
            "_review_action": "approved" if is_hand_curated else "pending",
            "_abbrev_entry": True,
        })

    # 1. emit hand-curated definitions first
    for entry in hand_curated:
        emit(entry, is_hand_curated=True)

    # 2. emit stubs (for Stage 5 to enrich) for everything else
    for abbr, count in sorted(freq.items(), key=lambda x: -x[1]):
        slug = make_slug(abbr)

        if abbr in config.ABBREV_SKIP:
            report_rows.append((abbr, count, "skipped_noise"))
            continue
        if re.match(r'^NCT\d+', abbr) or re.match(r'^[A-Z]{2,4}\d{4,}', abbr):
            report_rows.append((abbr, count, "skipped_noise"))
            continue
        if slug in wiki_slugs:
            report_rows.append((abbr, count, "in_keywords"))
            continue
        if slug in seen_slugs:
            report_rows.append((abbr, count, "in_keywords"))   # covered by hand-curated def
            continue

        emit(make_stub(abbr, count), count)
        report_rows.append((abbr, count, "missing"))

    # ── write output ──────────────────────────────────────────────────────────
    with open(args.output, "w", encoding="utf-8") as f:
        for row in output_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(args.report, "w", encoding="utf-8") as f:
        f.write("abbr,count,status\n")
        for abbr, count, status in report_rows:
            f.write(f'"{abbr}",{count},{status}\n')

    hand_written = sum(1 for r in output_rows if r["_review_action"] == "approved")
    stubs = sum(1 for r in output_rows if r["_review_action"] == "pending")
    print(f"\nTotal written   : {len(output_rows)}")
    print(f"  Hand-curated  : {hand_written}")
    print(f"  Stubs (need Stage 5 enrichment): {stubs}")
    print(f"Output          : {args.output}")
    print(f"Audit report    : {args.report}")


if __name__ == "__main__":
    main()
