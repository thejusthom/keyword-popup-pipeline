#!/usr/bin/env python3
"""
generate_config.py — Stage 0: scan the textbook and draft config.py for you.

This is the recommended starting point for a new subject. The textbook's own
MDX files are needed later anyway (Stage 4 scans them for abbreviations) —
this stage scans them FIRST and uses their structure (chapter/section titles,
meta.json contents, content samples) plus an LLM call to draft:

  - DOMAIN_NAME, DOMAIN_DESCRIPTION
  - CATEGORY_LIST, CATEGORY_KEYWORDS, WIKI_CAT_MAP, DEFAULT_CATEGORY
  - a categories.txt draft (full, exact Wikipedia category names — Stage 1
    matches these exactly, not as substrings, so this needs real category
    names, not short fragments; see Stage 1's own docstring for why)
  - ABBREV_CATEGORY_RULES, DEFAULT_ABBREV_CATEGORY
  - BIO_KEYWORDS (the generic on-subject fallback probe)
  - 4 draft few-shot worked examples for Stage 3's rewrite_wiki.py

None of this is written to your real config.py automatically unless you pass
--apply — by default everything goes to review files first
(config.generated.py, categories.generated.txt, few_shot_examples.generated.txt)
so you can read and correct the LLM's guesses before committing to them.

As a byproduct, this stage also caches the raw abbreviation frequency scan
(textbook_scan_cache.json) so Stage 4 doesn't have to re-walk every MDX file
from scratch.

Usage:
    export OPENAI_API_KEY=sk-...
    python generate_config.py --docs ../docs
    python generate_config.py --docs ../docs --dry-run     # no API call, verify scanning works
    python generate_config.py --docs ../docs --apply       # write straight to ../config.py + ../categories.txt

ALTERNATIVE — no script needed:
    You don't have to run this as a script at all. You can just paste your
    textbook's MDX files (or a representative sample — a few chapters is
    usually enough) directly into a chat with an LLM and ask it to draft
    config.py using the template and instructions in this repo's README.
    This script exists to automate that same process, not replace the option
    of doing it by hand in conversation.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_dotenv():
    for p in [Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"]:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        os.environ.setdefault(key, value)
            return

load_dotenv()


# ── 1. SCAN THE TEXTBOOK ──────────────────────────────────────────────────────

def strip_mdx(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)          # JSX/HTML tags
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)  # code fences
    text = re.sub(r"`[^`]*`", " ", text)           # inline code
    text = re.sub(r"import .*|export .*", " ", text)  # MDX import/export lines
    return text


def extract_title(text: str, filename: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # fall back to filename: "6_Tissue_Organization_And_Organ_Systems.mdx" -> "Tissue Organization And Organ Systems"
    stem = Path(filename).stem
    stem = re.sub(r"^\d+_", "", stem)
    return stem.replace("_", " ").strip()


def scan_textbook(docs_root: Path, sample_chars: int = 500) -> dict:
    chapters = {}   # folder name -> list of {filename, title, sample}
    meta_files = {}  # folder name -> parsed meta.json content
    freq = Counter()

    mdx_files = sorted(docs_root.rglob("*.mdx")) + sorted(docs_root.rglob("*.md"))
    for mdx in mdx_files:
        folder = mdx.parent.name
        raw = mdx.read_text(errors="ignore")
        clean = strip_mdx(raw)

        title = extract_title(clean, mdx.name)
        sample = " ".join(clean.split())[:sample_chars]

        chapters.setdefault(folder, []).append({
            "filename": mdx.name,
            "title": title,
            "sample": sample,
        })

        for m in re.finditer(r"\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b", raw):
            freq[m.group(1)] += 1

    for meta_path in sorted(docs_root.rglob("meta.json")):
        try:
            meta_files[meta_path.parent.name] = json.loads(meta_path.read_text(errors="ignore"))
        except json.JSONDecodeError:
            continue

    return {
        "chapters": chapters,
        "meta_files": meta_files,
        "file_count": len(mdx_files),
        "top_abbreviations": [abbr for abbr, _ in freq.most_common(80)],
        "abbrev_freq": dict(freq),
    }


def profile_to_text(profile: dict, max_chapters: int = 40) -> str:
    """Turn the scan into a compact text block for the LLM prompt."""
    lines = [f"Textbook has {profile['file_count']} content files across "
             f"{len(profile['chapters'])} chapters/sections.\n"]

    for i, (folder, files) in enumerate(profile["chapters"].items()):
        if i >= max_chapters:
            lines.append(f"... and {len(profile['chapters']) - max_chapters} more chapters/sections")
            break
        lines.append(f"\n### {folder}")
        if folder in profile["meta_files"]:
            lines.append(f"meta.json: {json.dumps(profile['meta_files'][folder])[:300]}")
        for f in files[:6]:   # cap files per chapter shown to the LLM
            lines.append(f"  - {f['title']}: {f['sample'][:200]}")

    if profile["top_abbreviations"]:
        lines.append(f"\nMost frequent abbreviations found in the text: "
                      f"{', '.join(profile['top_abbreviations'][:50])}")

    return "\n".join(lines)


# ── 2. LLM CALL ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are configuring a keyword-extraction pipeline for a textbook.
Given the textbook's chapter/section structure and content samples, infer the
subject and produce a configuration. Be specific and grounded in what's
actually in the textbook, not generic guesses.

Return ONLY valid JSON with exactly these keys:

{
  "domain_name": "short 2-6 word subject name",
  "domain_description": "one sentence describing the full scope, used to tell an LLM what counts as on-subject",
  "textbook_platform_name": "a reasonable guess at a platform name, or 'Textbook' if unclear",
  "category_list": ["8-15 category labels appropriate for classifying keywords in this textbook"],
  "default_category": "one entry from category_list to use as a fallback",
  "category_keywords": {"Category Name": ["15-25 lowercase keyword/phrase substrings each"], ...},
  "wiki_cat_map": {"lowercase wikipedia category substring": "Category Name from category_list", ...},
  "wikipedia_categories": ["200-500 FULL, EXACT Wikipedia category names as they actually appear after 'Category:' on Wikipedia, e.g. 'Lung cancer', 'Breast cancer', 'Cancer research' as three separate entries — NOT short fragments like just 'cancer'. Stage 1 matches these exactly (case-insensitive), not as substrings, so enumerate every specific category variant you can think of rather than a handful of root words. Err toward more entries, not broader ones."],
  "bio_keywords": ["15-25 generic lowercase terms used as a last-resort on-subject-at-all check"],
  "abbrev_category_rules": [["Category Name", ["ABBR1", "ABBR2", "..."]], ...],
  "default_abbrev_category": "one entry from category_list",
  "few_shot_examples": [
    {
      "label": "Disambiguation resolved",
      "keyword": "...", "wiki_source_text": "...", "wiki_categories": [],
      "output": {"relevant": true, "category": "...", "definition_short": "...", "definition_raw": "..."}
    },
    {"label": "Off-subject term correctly rejected", "keyword": "...", "wiki_source_text": "...", "wiki_categories": [],
      "output": {"relevant": false, "irrelevant_reason": "...", "category": "...", "definition_short": "", "definition_raw": ""}},
    {"label": "Thin source enriched", "keyword": "...", "wiki_source_text": "...", "wiki_categories": [],
      "output": {"relevant": true, "category": "...", "definition_short": "...", "definition_raw": "..."}},
    {"label": "Formatting artifacts stripped", "keyword": "...", "wiki_source_text": "...", "wiki_categories": [],
      "output": {"relevant": true, "category": "...", "definition_short": "...", "definition_raw": "..."}}
  ]
}

Use real terms and plausible Wikipedia-style source text for the few_shot_examples,
grounded in the textbook's actual subject matter (use the frequent abbreviations
list you were given as inspiration for at least one example).
"""


def call_llm(profile_text: str, model: str) -> dict:
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        max_tokens=4096,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Textbook structure:\n\n{profile_text}"},
        ],
    )
    return json.loads(resp.choices[0].message.content)


def dry_run_stub(profile: dict) -> dict:
    """No API call — returns a minimal, obviously-placeholder config so the
    rest of this stage's plumbing (file writing, formatting) can be verified
    for free."""
    return {
        "domain_name": "DRY-RUN placeholder subject",
        "domain_description": "DRY-RUN placeholder description — replace by running without --dry-run",
        "textbook_platform_name": "Textbook",
        "category_list": ["General Topic A", "General Topic B"],
        "default_category": "General Topic A",
        "category_keywords": {
            "General Topic A": ["placeholder", "term"],
            "General Topic B": ["placeholder", "term"],
        },
        "wiki_cat_map": {"placeholder": "General Topic A"},
        "wikipedia_categories": ["Placeholder exact category name"],
        "bio_keywords": ["placeholder"],
        "abbrev_category_rules": [["General Topic A", ["ABC"]]],
        "default_abbrev_category": "General Topic A",
        "few_shot_examples": [],
    }


# ── 3. WRITE OUTPUTS ─────────────────────────────────────────────────────────

CONFIG_TEMPLATE = '''"""
config.py — generated by 00_config_generator/generate_config.py

REVIEW THIS FILE BEFORE USING IT. It was drafted by an LLM from a scan of
your textbook's structure and content samples — check the category list and
keyword lists make sense for your subject, and especially check
`few_shot_examples.generated.txt` (not auto-applied — see README) before
running Stage 3.
"""

# ── 1. SUBJECT IDENTITY ────────────────────────────────────────────────────
DOMAIN_NAME = {domain_name!r}
DOMAIN_DESCRIPTION = {domain_description!r}
TEXTBOOK_PLATFORM_NAME = {textbook_platform_name!r}

# ── 2. PATHS ────────────────────────────────────────────────────────────────
DOCS_ROOT = {docs_root!r}

# ── 3. CATEGORIES ───────────────────────────────────────────────────────────
CATEGORY_LIST = {category_list!r}
DEFAULT_CATEGORY = {default_category!r}
WIKI_CAT_MAP = {wiki_cat_map!r}
CATEGORY_KEYWORDS = {category_keywords!r}

# ── 4. RELEVANCE FILTER ─────────────────────────────────────────────────────
# Left as generic defaults — review these against your subject.
NON_BIO_CONTENT_SIGNALS = [
    'is a city in', 'is a town in', 'is a village in', 'is a municipality',
    'was an american', 'was a british', 'was a writer', 'was a poet',
    'was a composer', 'was a politician', 'was a novelist',
    'is a professional football', 'is a football club', 'is a military',
    'is a missile', 'is a warship',
]
NON_BIO_CAT_SIGNALS = [
    'plays by ', 'novels by ', 'films by ', 'albums by ', 'songs by ',
    'television series', 'real estate', 'populated places',
]
NON_BIO_KW_PATTERNS = [
    r'\\(play\\)$', r'\\(film\\)$', r'\\(novel\\)$', r'\\(album\\)$', r'\\(song\\)$',
]
IRRELEVANT_SIGNALS = ['may refer to:', 'refers to:', 'set index article', 'disambiguation']
BIO_KEYWORDS = {bio_keywords!r}

# ── 5. ABBREVIATION SCANNING ─────────────────────────────────────────────────
ABBREV_SKIP = {{
    "MDX", "JSX", "TSX", "HTML", "CSS", "JSON", "API", "URL", "HTTP", "HTTPS",
    "NULL", "TRUE", "FALSE", "EOF", "TODO", "FIXME",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII", "XIV",
    "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "AND", "NOT", "MIN", "OFF", "USE", "TERM", "NOTES", "TEXT", "BEING", "LED",
    "TB", "ML", "EU", "UK", "USA", "HM", "NT", "GE", "OK", "ST", "CC", "SF",
}}
ABBREV_CATEGORY_RULES = {abbrev_category_rules!r}
DEFAULT_ABBREV_CATEGORY = {default_abbrev_category!r}
ABBREV_DEFS_MODULE = None

# ── 6. ABBREVIATION ENRICHMENT ───────────────────────────────────────────────
KNOWLEDGE_API = "none"   # set to "gene_ontology" only if your subject is biomedical
KNOWLEDGE_API_CATEGORIES = set()

# ── 7. LLM SETTINGS ───────────────────────────────────────────────────────────
LLM_PROVIDER = "openai"
WIKI_REWRITE_MODEL = "gpt-4o-mini"
ABBREV_ENRICH_MODEL = "gpt-4o-mini"
'''


def write_config(cfg: dict, docs_root: str, output_path: Path):
    content = CONFIG_TEMPLATE.format(
        domain_name=cfg["domain_name"],
        domain_description=cfg["domain_description"],
        textbook_platform_name=cfg["textbook_platform_name"],
        docs_root=docs_root,
        category_list=cfg["category_list"],
        default_category=cfg["default_category"],
        wiki_cat_map=cfg["wiki_cat_map"],
        category_keywords=cfg["category_keywords"],
        bio_keywords=cfg["bio_keywords"],
        abbrev_category_rules=[tuple(r) for r in cfg["abbrev_category_rules"]],
        default_abbrev_category=cfg["default_abbrev_category"],
    )
    output_path.write_text(content, encoding="utf-8")


def write_categories_txt(cfg: dict, output_path: Path):
    lines = cfg.get("wikipedia_categories", [])
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_few_shot(cfg: dict, output_path: Path):
    examples = cfg.get("few_shot_examples", [])
    if not examples:
        output_path.write_text(
            "# No few-shot examples were generated (likely a --dry-run). "
            "Re-run without --dry-run, or write these by hand — see the "
            "module docstring in 03_wiki_rewrite/rewrite_wiki.py.\n",
            encoding="utf-8",
        )
        return

    blocks = []
    for i, ex in enumerate(examples, 1):
        out = ex.get("output", {})
        out_json = json.dumps(out, indent=2, ensure_ascii=False)
        blocks.append(
            f"--- Example {i}: {ex.get('label', '')} ---\n"
            f"Keyword: {ex.get('keyword', '')}\n"
            f"Wikipedia source text:\n{ex.get('wiki_source_text', '')}\n"
            f"Wikipedia categories: {ex.get('wiki_categories', [])}\n\n"
            f"Output:\n{out_json}\n"
        )

    header = (
        "# Draft few-shot examples generated from your textbook.\n"
        "# COPY THE BLOCKS BELOW into the _FEW_SHOT string in\n"
        "# 03_wiki_rewrite/rewrite_wiki.py, replacing the oncology examples there.\n"
        "# Review each one for accuracy before using — the LLM drafted these from\n"
        "# your textbook's structure, it did not verify them against real Wikipedia articles.\n\n"
    )
    output_path.write_text(header + "\n\n".join(blocks), encoding="utf-8")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Stage 0: scan textbook, draft config.py")
    p.add_argument('--docs', default='../docs', help='Textbook MDX/MD source directory')
    p.add_argument('--model', default='gpt-4o-mini')
    p.add_argument('--dry-run', action='store_true', help='No API call, verify scanning + file writing only')
    p.add_argument('--apply', action='store_true',
                    help='Write directly to ../config.py and ../categories.txt instead of *.generated files')
    p.add_argument('--cache', default='textbook_scan_cache.json',
                    help='Where to cache the raw scan (Stage 4 can reuse this)')
    args = p.parse_args()

    docs_root = Path(args.docs)
    if not docs_root.exists():
        print(f"ERROR: docs directory not found: {docs_root}", file=sys.stderr)
        print("Paste your textbook's MDX files into that directory first, "
              "or point --docs at wherever they already live.", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {docs_root} ...")
    profile = scan_textbook(docs_root)
    print(f"  {profile['file_count']} files across {len(profile['chapters'])} chapters/sections")
    print(f"  {len(profile['top_abbreviations'])} distinct abbreviation candidates found")

    with open(args.cache, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False)
    print(f"  Cached raw scan -> {args.cache} (Stage 4 can reuse this instead of rescanning)")

    if args.dry_run:
        print("\n[DRY RUN] Skipping LLM call.")
        cfg = dry_run_stub(profile)
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            print("ERROR: OPENAI_API_KEY not set (env var or .env file)", file=sys.stderr)
            sys.exit(1)
        profile_text = profile_to_text(profile)
        print(f"\nCalling {args.model} to draft a config from the scan...")
        cfg = call_llm(profile_text, args.model)

    config_out = Path("../config.py" if args.apply else "config.generated.py")
    categories_out = Path("../categories.txt" if args.apply else "categories.generated.txt")
    few_shot_out = Path("few_shot_examples.generated.txt")

    write_config(cfg, args.docs, config_out)
    write_categories_txt(cfg, categories_out)
    write_few_shot(cfg, few_shot_out)

    print(f"\nWrote:")
    print(f"  {config_out}")
    print(f"  {categories_out}")
    print(f"  {few_shot_out}  (copy into rewrite_wiki.py by hand — see file header)")

    if not args.apply:
        print(f"\nReview these *.generated files, then rename/copy them over "
              f"../config.py and ../categories.txt when you're happy with them.")
    print(f"\nDetected domain: {cfg['domain_name']}")
    print(f"Categories: {', '.join(cfg['category_list'])}")


if __name__ == "__main__":
    main()
