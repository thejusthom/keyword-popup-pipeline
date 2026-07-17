#!/usr/bin/env python3
"""
enrich_abbrevs.py — Stage 5: LLM writes real definitions for every abbreviation.

For each abbreviation stub from Stage 4, combines:
  1. Textbook context  — real sentences from the MDX files showing how the
     abbreviation is actually used
  2. External knowledge API (optional) — e.g. Gene Ontology, if configured
  3. An LLM call that writes an encyclopedia-style definition

Usage:
    export OPENAI_API_KEY=sk-...
    python enrich_abbrevs.py                      # full run
    python enrich_abbrevs.py --limit 5             # smoke test, 5 entries
    python enrich_abbrevs.py --dry-run --limit 5   # no API calls, verify plumbing for free

Checkpoints every 25 entries to enrich_checkpoint.json — safe to re-run/resume.

All subject-specific values (domain name, docs directory, which external API
to query) live in ../config.py.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


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

MODEL = config.ABBREV_ENRICH_MODEL
MAX_CONTEXT_CHARS = 600
CHECKPOINT = "enrich_checkpoint.json"


def make_slug(kw: str) -> str:
    s = kw.lower().replace("(", "").replace(")", "")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


# ── context index ────────────────────────────────────────────────────────────

def build_context_index(docs_root: Path) -> dict:
    print(f"Indexing MDX files under {docs_root} for context snippets...")
    context_index: dict[str, list[str]] = {}
    files = list(docs_root.rglob("*.mdx")) + list(docs_root.rglob("*.md"))
    for mdx in files:
        raw = mdx.read_text(errors="ignore")
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"`[^`]*`", " ", text)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sent in sentences:
            for m in re.finditer(r"\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b", sent):
                abbr = m.group(1)
                bucket = context_index.setdefault(abbr, [])
                if len(bucket) < 4:
                    cleaned = sent.strip()
                    if 20 < len(cleaned) < 300:
                        bucket.append(cleaned)
    print(f"  Indexed context for {len(context_index)} abbreviations across {len(files)} files")
    return context_index


# ── external knowledge API (optional, see config.KNOWLEDGE_API) ─────────────

def go_lookup(term: str) -> str:
    """Gene Ontology lookup — used when config.KNOWLEDGE_API == 'gene_ontology'."""
    try:
        url = ("https://api.geneontology.org/api/search/entity/autocomplete/"
               + urllib.parse.quote(term) + "?rows=1&start=0&highlight_class=hilite")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        docs = data.get("docs", [])
        if not docs:
            return ""
        hit = docs[0]
        label = hit.get("label", [""])[0] if isinstance(hit.get("label"), list) else hit.get("label", "")
        defn = hit.get("definition", [""])[0] if isinstance(hit.get("definition"), list) else hit.get("definition", "")
        return f"GO: {label} -- {defn}" if (label and defn) else ""
    except Exception:
        return ""


def knowledge_lookup(term: str, category: str) -> str:
    if config.KNOWLEDGE_API == "gene_ontology" and category in config.KNOWLEDGE_API_CATEGORIES:
        return go_lookup(term)
    return ""


# ── LLM call ─────────────────────────────────────────────────────────────────

SYSTEM = f"""You are a lexicographer writing definitions for a {config.DOMAIN_NAME} textbook.
Given an abbreviation, its full name, its field, optional external reference info, and 1-3
sentences of textbook context, write a clear, factual definition in the style of an
encyclopedia entry.

Rules:
- Start with the full name followed by the abbreviation in parentheses.
- 2-4 sentences. No bullet points.
- definition_short: first 1-2 sentences, max 280 characters.
- definition_raw: complete 2-4 sentence definition.
- Return JSON only: {{"definition_short": "...", "definition_raw": "..."}}
"""


def get_definition(client, keyword, full_name, category, external_info, context):
    ctx_text = " ".join(context[:3]) if context else ""
    user_msg = f"Abbreviation: {keyword}\nFull name: {full_name}\nField: {category}\n"
    if external_info:
        user_msg += f"External reference info: {external_info}\n"
    if ctx_text:
        user_msg += f"Textbook context: {ctx_text[:MAX_CONTEXT_CHARS]}\n"
    user_msg += "\nWrite the definition JSON."

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def get_definition_dry_run(keyword, full_name, category, external_info, context):
    return {
        "definition_short": f"{keyword} ({full_name}) is a DRY-RUN placeholder definition.",
        "definition_raw": f"{keyword} ({full_name}) is a DRY-RUN placeholder definition used to "
                           f"verify pipeline plumbing without spending API credits.",
    }


def load_checkpoint():
    if Path(CHECKPOINT).exists():
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {}


def save_checkpoint(done: dict):
    with open(CHECKPOINT, "w") as f:
        json.dump(done, f)


def main():
    p = argparse.ArgumentParser(description="Stage 5: LLM-enrich abbreviation definitions")
    p.add_argument('--input', default='../04_abbrev_scan/keywords_abbrevs.jsonl')
    p.add_argument('--output', default='keywords_abbrevs_enriched.jsonl')
    p.add_argument('--docs', default=config.DOCS_ROOT)
    p.add_argument('--limit', type=int, default=0, help='Only process the first N entries')
    p.add_argument('--dry-run', action='store_true', help='No API calls -- writes placeholders for free')
    args = p.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    docs_root = Path(args.docs)
    context_index = build_context_index(docs_root) if docs_root.exists() else {}

    client = None
    if not args.dry_run:
        try:
            import openai
        except ImportError:
            print("ERROR: pip install openai", file=sys.stderr)
            sys.exit(1)
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        if not os.environ.get("OPENAI_API_KEY"):
            print("ERROR: OPENAI_API_KEY not set (env var or .env file)", file=sys.stderr)
            sys.exit(1)

    rows = [json.loads(l) for l in open(input_path) if l.strip()]
    if args.limit:
        rows = rows[: args.limit]

    done = load_checkpoint()
    total = len(rows)
    print(f"Processing {total} abbreviations ({len(done)} already done)"
          f"{' [DRY RUN]' if args.dry_run else ''}...\n")

    for i, row in enumerate(rows):
        slug = make_slug(row["keyword"])
        if slug in done:
            continue

        keyword = row["keyword"]
        full_name = (row.get("categories") or [keyword])[0]
        category = row.get("category", config.DEFAULT_ABBREV_CATEGORY)
        context = context_index.get(keyword, [])
        external_info = knowledge_lookup(full_name if full_name != keyword else keyword, category)

        try:
            if args.dry_run:
                defn = get_definition_dry_run(keyword, full_name, category, external_info, context)
            else:
                defn = get_definition(client, keyword, full_name, category, external_info, context)
            row["definition_short"] = defn.get("definition_short", row["definition_short"])
            row["definition_raw"]   = defn.get("definition_raw", row["definition_raw"])
            row["_review_action"]   = "approved"
            done[slug] = row
            status = "OK"
        except Exception as e:
            status = f"FAILED: {e}"

        print(f"[{i + 1}/{total}] {keyword:20s} {status}")

        if (i + 1) % 25 == 0:
            save_checkpoint(done)

    save_checkpoint(done)

    slug_to_enriched = {make_slug(r["keyword"]): r for r in done.values()}
    with open(args.output, "w") as f:
        for row in rows:
            slug = make_slug(row["keyword"])
            out = slug_to_enriched.get(slug, row)
            f.write(json.dumps(out) + "\n")

    print(f"\nDone. Wrote {len(rows)} rows to {args.output}")
    print(f"Checkpoint saved to {CHECKPOINT} -- re-run anytime to resume.")


if __name__ == "__main__":
    main()
