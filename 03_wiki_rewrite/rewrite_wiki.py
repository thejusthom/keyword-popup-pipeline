#!/usr/bin/env python3
"""
rewrite_wiki.py — Stage 3: LLM rewrites every definition properly.

This is the step that matters most for quality. Reads Stage 2's output
(keywords_clean.jsonl-equivalent) and, for each entry, has the LLM:
  - resolve disambiguation pages to the on-subject meaning
  - make a strict relevant/not-relevant call
  - assign one category
  - write a fresh definition_short + definition_raw from its own knowledge,
    using the Wikipedia text only as a factual anchor, not something to copy

Costs real money — every entry is an API call. Use --limit to test cheaply
before committing to a full run; the script prints an estimated cost before
starting a run of any size.

Usage:
    export OPENAI_API_KEY=sk-...
    python rewrite_wiki.py                                    # full run
    python rewrite_wiki.py --limit 5                           # smoke test, 5 entries
    python rewrite_wiki.py --model gpt-4o                      # higher quality, higher cost
    python rewrite_wiki.py --resume                            # continue an interrupted run
    python rewrite_wiki.py --dry-run --limit 5                 # no API calls at all —
                                                                   verifies the pipeline plumbing
                                                                   (I/O, JSON shape) without spending money

ADAPTING TO A NEW SUBJECT:
    The system prompt below is built from config.py (DOMAIN_NAME,
    DOMAIN_DESCRIPTION, CATEGORY_LIST) — edit those, not this file, for most
    changes.

    The one piece that genuinely can't be generalized: the "_FEW_SHOT" block
    a few dozen lines down contains 4 worked examples (input Wikipedia text +
    expected JSON output) that the model learns from. These are written for
    an oncology textbook. For a new subject, replace them with your own 3-4
    worked examples showing: (1) a disambiguation page correctly resolved,
    (2) an off-subject term correctly rejected, (3) a thin/short source
    entry correctly enriched, (4) an entry with formatting artifacts
    correctly cleaned. Good worked examples matter more than any other
    single lever for output quality at this step.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

# The openai package is only required for a real (non---dry-run) run, so the
# import failure is deferred to main() — this lets --dry-run work on a
# machine that doesn't have the package installed yet, useful for testing
# the rest of the pipeline's plumbing before setting up API access.
try:
    from openai import OpenAI, RateLimitError, APIError, BadRequestError
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
    class RateLimitError(Exception): pass
    class APIError(Exception): pass
    class BadRequestError(Exception): pass

# ── CONFIG ───────────────────────────────────────────────────────────────────

DEFAULT_INPUT   = "../02_wiki_clean/keywords_clean.jsonl"
DEFAULT_OUTPUT  = "keywords_v3.jsonl"
DEFAULT_FLAGGED = "flagged_for_review.jsonl"
LOG_FILE        = "v3_processing.log"

DEFAULT_MODEL   = config.WIKI_REWRITE_MODEL
MAX_RETRIES     = 2
THIN_THRESHOLD  = 50
API_DELAY       = 0.3
API_ERROR_DELAY = 10

# Rough per-entry cost estimates for the cost preview (USD, input+output tokens combined)
_COST_PER_ENTRY = {
    "gpt-4o":      0.0155,
    "gpt-4o-mini": 0.0009,
}

# ── ARTIFACT / QUALITY CHECKER ───────────────────────────────────────────────

_ARTIFACT_PATTERNS = [
    (r'\{\{|\}\}',            'wiki template markup {{ }}'),
    (r'\[\[|\]\]',            'wiki link markup [[ ]]'),
    (r'<ref',                 'reference tag <ref>'),
    (r'thumb\|',               'image thumb markup'),
    (r'\b\d+px\b',             'pixel dimension (e.g. 320px)'),
    (r'&[a-z]{2,8};',          'HTML entity (e.g. &amp;)'),
    (r'&#\d+;',                'numeric HTML entity'),
    (r'\(:\s*\w',              'plural notation  (: amnions)'),
    (r'\(\s*/[^)]{2,}/\s*\)',  'pronunciation guide (/ ... /)'),
    (r'\(\s*\)',               'empty parentheses ()'),
    (r'(?i)can refer to:',     'disambiguation phrase "can refer to:"'),
    (r'(?i)may refer to:',     'disambiguation phrase "may refer to:"'),
    (r'(?m)^\s*[*\-]\s',       'bullet list line'),
    (r'\[\d+\]',               'inline citation [1]'),
]

def _find_artifacts(text: str) -> list:
    return [desc for pattern, desc in _ARTIFACT_PATTERNS if re.search(pattern, text)]

def check_quality(keyword: str, result: dict) -> list:
    if not result.get('relevant'):
        return []
    failures = []
    short = result.get('definition_short', '')
    raw   = result.get('definition_raw', '')
    kw8   = keyword.lower()[:8]

    if not short:
        failures.append('definition_short is empty')
    else:
        kw_core = re.sub(r'\s*\(.*?\)', '', keyword).strip().lower()
        if kw_core not in short[:80].lower():
            failures.append(f'keyword "{keyword}" not found in first 80 chars of definition_short')
        if len(short) < 50:
            failures.append(f'definition_short too short ({len(short)} chars, need >=50)')
        if len(short) > 450:
            failures.append(f'definition_short too long ({len(short)} chars, need <=450)')
        for desc in _find_artifacts(short):
            failures.append(f'Artifact in definition_short: {desc}')

    if not raw:
        failures.append('definition_raw is empty')
    else:
        para_count = len([p for p in raw.split('\n\n') if p.strip()])
        if para_count < 3:
            failures.append(f'definition_raw needs >=3 paragraphs (found {para_count})')
        if len(raw) < 300:
            failures.append(f'definition_raw too short ({len(raw)} chars, need >=300)')
        for desc in _find_artifacts(raw):
            failures.append(f'Artifact in definition_raw: {desc}')

    return failures

# ── PROMPTS (built from config.py) ───────────────────────────────────────────

SYSTEM_PROMPT = (
    f"You are an education writer for {config.TEXTBOOK_PLATFORM_NAME}, a textbook platform "
    f"for students studying {config.DOMAIN_DESCRIPTION}.\n\n"
    "Your task is to evaluate Wikipedia keyword entries and produce clean, accurate, "
    "student-ready definitions. You write from your own expert knowledge, using the Wikipedia "
    "source text only as a factual reference — never as prose to copy or paraphrase directly.\n\n"
    "The definitions appear in two places:\n"
    "  - definition_short — a popup tooltip when a student clicks a keyword\n"
    "  - definition_raw   — a full article modal when the student clicks \"Read more\"\n\n"
    "Both must read as polished educational content, not encyclopedia entries.\n"
    "Always return valid JSON and nothing else."
)

# ── worked examples — SUBJECT-SPECIFIC, see module docstring above ──────────
_FEW_SHOT = """
=== WORKED EXAMPLES ===

--- Example 1: Disambiguation resolved ---
Keyword: CTL
Wikipedia source text:
CTL can refer to: Champions Tennis League, a tennis championship league in India. Chronic training load, cumulative sports training. Circuit Total Limitation, US standard for electrical panels. Cytotoxic T lymphocyte. Cut-to-length logging.
Wikipedia categories: []

Output:
{
  "relevant": true,
  "category": "Immunology",
  "definition_short": "CTL, or cytotoxic T lymphocyte, is a CD8+ immune cell that kills virus-infected and cancerous cells by releasing perforin and granzymes upon direct contact. CTLs are the primary effectors of anti-tumor immunity and the cellular basis of cancer immunotherapies such as checkpoint inhibitors and CAR-T therapy.",
  "definition_raw": "Cytotoxic T lymphocytes (CTLs), also called CD8+ T cells, are a specialized subset of T lymphocytes whose primary function is to seek out and destroy cells displaying abnormal antigens.\\n\\nCTLs recognize their targets through the T cell receptor (TCR), which binds to antigen fragments presented on MHC class I molecules found on virtually all nucleated cells.\\n\\nIn oncology, CTLs are a critical component of anti-tumor immunity."
}

--- Example 2: Off-subject term correctly rejected ---
Keyword: The Selfish Gene
Wikipedia source text:
The Selfish Gene is a 1976 book by ethologist Richard Dawkins that promotes the gene-centered view of evolution.
Wikipedia categories: [Books by Richard Dawkins, Popular science books]

Output:
{
  "relevant": false,
  "irrelevant_reason": "This is a popular science book, not a subject-relevant term or concept.",
  "category": "Molecular Biology",
  "definition_short": "",
  "definition_raw": ""
}

--- Example 3: Thin source enriched ---
Keyword: AIDS-related lymphoma
Wikipedia source text:
AIDS-related lymphoma describes lymphomas occurring in patients with acquired immunodeficiency syndrome (AIDS). A lymphoma is a type of cancer arising from lymphoid cells.
Wikipedia categories: [Oncology, HIV/AIDS]

Output:
{
  "relevant": true,
  "category": "Oncology",
  "definition_short": "AIDS-related lymphoma is a malignancy of the lymphatic system that arises in HIV/AIDS patients due to severe immunosuppression-driven B-cell transformation. It is classified as an AIDS-defining illness and is treated with combined antiretroviral therapy and aggressive chemotherapy regimens.",
  "definition_raw": "AIDS-related lymphoma (ARL) is a group of malignant cancers of the lymphatic system that arise in the setting of HIV infection.\\n\\nThe pathogenesis of ARL is closely tied to the immune collapse caused by HIV.\\n\\nClinically, ARL tends to present at advanced stage with frequent extranodal involvement."
}

--- Example 4: Formatting artifacts stripped ---
Keyword: Amnion
Wikipedia source text:
The amnion (: amnions or amnia) is a membrane that closely covers human and various other embryos when they first form.
Wikipedia categories: [Embryology, Cell Biology]

Output:
{
  "relevant": true,
  "category": "Anatomy",
  "definition_short": "The amnion is a thin membrane enclosing the developing embryo within amniotic fluid, providing mechanical cushioning and a sterile environment throughout pregnancy. It is clinically important in obstetrics and prenatal diagnostics, including amniocentesis for chromosomal screening.",
  "definition_raw": "The amnion is the innermost of the fetal membranes, forming a fluid-filled sac that directly envelops the developing embryo.\\n\\nAmniotic fluid serves multiple protective functions.\\n\\nClinically, the amnion and its fluid are important in prenatal diagnosis."
}

=== END EXAMPLES ===
"""

def _build_user_prompt(keyword: str, source_text: str, categories: list, retry_note: str = "") -> str:
    cat_str = ", ".join(categories) if categories else "none"
    retry_block = (
        "\n\n RETRY — your previous response failed these quality checks:\n"
        + "\n".join(f"  - {f}" for f in retry_note.split("\n") if f.strip())
        + "\nFix every item above in this response.\n"
    ) if retry_note else ""

    category_options = " | ".join(config.CATEGORY_LIST)

    return f"""{_FEW_SHOT}
---

Now process this entry:{retry_block}

Keyword: {keyword}
Wikipedia source text:
{source_text}

Wikipedia categories: {cat_str}

---

STEP 1 -- DISAMBIGUATION CHECK
If the source text begins with "X can refer to:", "X may refer to:", or a bullet list of
unrelated meanings, scan for an on-subject meaning and write definitions for THAT meaning only.
If none exists -> relevant=false.

STEP 2 -- RELEVANCE
Mark relevant=false (no exceptions) for:
  - Books, films, TV shows, plays, albums, songs -- even if thematically related
  - Biographies of individual people (exception: named concepts like "HeLa cells" are fine)
  - Pure thought experiments with no direct mechanism relevant to {config.DOMAIN_NAME}
  - Content clearly outside {config.DOMAIN_NAME}
  - Wikipedia template or navigation pages

Mark relevant=true for anything squarely within: {config.DOMAIN_DESCRIPTION}

When uncertain -> lean toward relevant=true.

STEP 3 -- CATEGORY (pick exactly one): {category_options}

STEP 4 -- definition_short (popup tooltip)
  - Exactly 2 sentences -- no more, no fewer
  - Target length: 120-200 characters per sentence
  - Sentence 1: What it IS -- open with "{keyword} is..." or "A/An/The {keyword} is..."
  - Sentence 2: Why it matters -- one key point of significance
  - NEVER include: (/ phonetics /), (: plural), (), wiki markup, "can refer to",
    inline citations [1], bullet lists

STEP 5 -- definition_raw (full article)
  - Minimum 3 paragraphs separated by \\n\\n
  - Structure: paragraph 1 = identity/classification, paragraph 2 = mechanism/detail,
    paragraph 3+ = significance within {config.DOMAIN_NAME}
  - Enrich from your own knowledge -- do NOT be limited by the Wikipedia source text
  - Same hard bans as definition_short; no bullet points or numbered lists

Return ONLY valid JSON -- no markdown fences, no commentary:
{{
  "relevant": true or false,
  "irrelevant_reason": "..." (only if relevant=false; omit key entirely if relevant=true),
  "category": "...",
  "definition_short": "...",
  "definition_raw": "..."
}}
If relevant=false -> definition_short="" and definition_raw="".
"""

# ── API CALL ─────────────────────────────────────────────────────────────────

def _call_openai(client, model, keyword, source, cats, retry_note=""):
    prompt = _build_user_prompt(keyword, source, cats, retry_note)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw.strip())

def _call_dry_run(keyword, source, cats, retry_note=""):
    """Stub used with --dry-run: no network call, returns a placeholder built
    long enough to pass check_quality() so a dry run completes cleanly and
    shows you what a full run's plumbing (I/O, JSON shape, flagging) looks
    like without spending any API credits."""
    category = next((c for c in cats if c in config.CATEGORY_LIST), config.DEFAULT_CATEGORY)
    short = (
        f"{keyword} is a DRY-RUN placeholder definition generated without calling the LLM. "
        f"This text exists only to verify the pipeline's plumbing end to end."
    )
    raw = (
        f"{keyword} is a DRY-RUN placeholder paragraph one, generated without calling the LLM, "
        f"long enough to pass the automated quality checker's length and paragraph-count rules.\n\n"
        f"This is DRY-RUN placeholder paragraph two, standing in for what would normally be a "
        f"real explanation of {keyword}'s mechanism or detail once a real run is performed.\n\n"
        f"This is DRY-RUN placeholder paragraph three, standing in for the significance section. "
        f"Re-run without --dry-run once you're ready to spend real API credits on {keyword}."
    )
    return {
        "relevant": True,
        "category": category,
        "definition_short": short,
        "definition_raw": raw,
    }

# ── MAIN PROCESSING ──────────────────────────────────────────────────────────

def process_entry(client, model: str, entry: dict, log, dry_run: bool = False) -> tuple:
    keyword = entry.get('keyword', '').strip()
    source  = entry.get('definition_raw', '')[:6000]
    cats    = list(set(entry.get('categories', []) + entry.get('all_page_categories', [])))
    thin    = len(entry.get('definition_raw', '').split()) < THIN_THRESHOLD

    result = None
    last_note = ""
    retry_failed = False

    for attempt in range(1 + MAX_RETRIES):
        try:
            if dry_run:
                result = _call_dry_run(keyword, source, cats, last_note)
            else:
                result = _call_openai(client, model, keyword, source, cats, last_note)
        except json.JSONDecodeError as e:
            last_note = f"Response was not valid JSON: {e}"
            log(f"    JSON error (attempt {attempt + 1}): {e}")
            if not dry_run:
                time.sleep(API_ERROR_DELAY)
            continue
        except RateLimitError:
            log(f"    Rate limited -- waiting 60s")
            time.sleep(60)
            continue
        except (APIError, BadRequestError) as e:
            log(f"    API error (attempt {attempt + 1}): {e}")
            if not dry_run:
                time.sleep(API_ERROR_DELAY)
            continue

        failures = check_quality(keyword, result)
        if not failures:
            break
        last_note = "\n".join(failures)
        log(f"    Quality check failed (attempt {attempt + 1}): {failures}")
        if not dry_run:
            time.sleep(API_DELAY)
    else:
        retry_failed = True
        log(f"    !! Retries exhausted for: {keyword}")

    if result is None:
        result = {
            "relevant": False,
            "irrelevant_reason": "Processing failed -- all API attempts errored",
            "category": config.DEFAULT_CATEGORY,
            "definition_short": "",
            "definition_raw": "",
        }
        retry_failed = True

    output = {k: v for k, v in entry.items()
              if k not in ('definition_short', 'definition_raw', 'category', 'relevant', 'irrelevant_reason')}
    output['keyword']          = keyword
    output['relevant']         = result.get('relevant', False)
    output['category']         = result.get('category', entry.get('category', config.DEFAULT_CATEGORY))
    output['definition_short'] = result.get('definition_short', '')
    output['definition_raw']   = result.get('definition_raw', '')
    if not result.get('relevant'):
        output['irrelevant_reason'] = result.get('irrelevant_reason', '')

    needs_review = thin or retry_failed
    if thin:
        output['_thin_source'] = True
    if retry_failed:
        output['_retry_exhausted'] = True
        output['_last_failures'] = last_note

    return output, needs_review

def load_done_keywords(path: Path) -> set:
    done = set()
    if path.exists():
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    done.add(json.loads(line)['keyword'])
                except Exception:
                    pass
    return done

# ── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 3: LLM rewrites every definition")
    parser.add_argument('--input',   default=DEFAULT_INPUT)
    parser.add_argument('--output',  default=DEFAULT_OUTPUT)
    parser.add_argument('--flagged', default=DEFAULT_FLAGGED)
    parser.add_argument('--model',   default=DEFAULT_MODEL,
                         help='OpenAI model (gpt-4o = higher quality/cost, gpt-4o-mini = cheaper)')
    parser.add_argument('--resume',  action='store_true',
                         help='Skip already-processed keywords in output file')
    parser.add_argument('--limit',   type=int, default=0,
                         help='Only process the first N entries (for testing)')
    parser.add_argument('--dry-run', action='store_true',
                         help='No API calls -- writes placeholder output to verify pipeline plumbing for free')
    args = parser.parse_args()

    input_path   = Path(args.input)
    output_path  = Path(args.output)
    flagged_path = Path(args.flagged)

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding='utf-8') as f:
        total_in_file = sum(1 for line in f if line.strip())
    total = min(args.limit, total_in_file) if args.limit else total_in_file

    est_per_entry = _COST_PER_ENTRY.get(args.model, 0.01)
    print(f"About to process {total} entries with model={args.model}"
          f"{' [DRY RUN — no API calls, no cost]' if args.dry_run else ''}")
    if not args.dry_run:
        print(f"Estimated cost: ~${total * est_per_entry:,.2f} (rough estimate, actual cost varies)")

    client = None
    if not args.dry_run:
        if not _OPENAI_AVAILABLE:
            print("ERROR: pip install openai (or use --dry-run to test without it)", file=sys.stderr)
            sys.exit(1)
        client = OpenAI()   # reads OPENAI_API_KEY from environment

    already_done = load_done_keywords(output_path) if args.resume else set()
    if already_done:
        print(f"Resuming -- {len(already_done)} entries already processed")

    log_file = open(LOG_FILE, 'a', encoding='utf-8')

    def log(msg: str):
        print(msg)
        log_file.write(msg + '\n')
        log_file.flush()

    write_mode = 'a' if args.resume else 'w'
    out_f     = open(output_path, write_mode, encoding='utf-8')
    flagged_f = open(flagged_path, write_mode, encoding='utf-8')

    processed = skipped = flagged = errors = 0

    try:
        with open(input_path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if args.limit and i > args.limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    log(f"[{i}/{total}] Skipping malformed line")
                    continue

                keyword = entry.get('keyword', '').strip()
                if keyword in already_done:
                    skipped += 1
                    continue

                log(f"[{i}/{total}] {keyword}")

                try:
                    output_entry, needs_review = process_entry(client, args.model, entry, log, dry_run=args.dry_run)
                    out_f.write(json.dumps(output_entry, ensure_ascii=False) + '\n')
                    out_f.flush()

                    if needs_review:
                        flagged_f.write(json.dumps(output_entry, ensure_ascii=False) + '\n')
                        flagged_f.flush()
                        flagged += 1
                        tag = []
                        if output_entry.get('_thin_source'):     tag.append('thin source')
                        if output_entry.get('_retry_exhausted'): tag.append('retries exhausted')
                        log(f"  -> flagged ({', '.join(tag)})")

                    processed += 1
                except Exception as e:
                    log(f"  !! Unhandled error on '{keyword}': {e}")
                    errors += 1

                if not args.dry_run:
                    time.sleep(API_DELAY)
    finally:
        out_f.close()
        flagged_f.close()
        summary = (
            f"\n{'='*60}\n"
            f"Done.\n"
            f"  Processed : {processed}\n"
            f"  Skipped   : {skipped}  (already done)\n"
            f"  Flagged   : {flagged}  -> {flagged_path}\n"
            f"  Errors    : {errors}\n"
            f"{'='*60}"
        )
        log(summary)
        log_file.close()

if __name__ == '__main__':
    main()
