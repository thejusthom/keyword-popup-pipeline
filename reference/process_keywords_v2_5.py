#!/usr/bin/env python3
"""
process_keywords_v2_5.py — Fill missing definitions in keywords_clean.jsonl

Most entries in keywords_clean.jsonl already have definitions — this script
passes those through unchanged (setting relevant=true). Only entries with a
missing or empty definition_short get an API call to generate one.

Result: keywords_v2.5.jsonl with all 10k entries, all relevant=true, all with definitions.

Usage:
    export OPENAI_API_KEY=sk-...
    python process_keywords_v2_5.py                          # full run, gpt-4o
    python process_keywords_v2_5.py --model gpt-4o-mini      # cheaper
    python process_keywords_v2_5.py --resume                 # continue interrupted run
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import os
from pathlib import Path as _Path

def _load_dotenv():
    for p in [_Path(__file__).parent / ".env", _Path.cwd() / ".env"]:
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
                        os.environ[key] = value
            return

_load_dotenv()

from openai import OpenAI, RateLimitError, APIError, BadRequestError

# ── CONFIG ───────────────────────────────────────────────────────────────────

DEFAULT_INPUT   = "keywords_clean.jsonl"
DEFAULT_V3      = "keywords_v3.jsonl"
DEFAULT_OUTPUT  = "keywords_v2.5.jsonl"
DEFAULT_FLAGGED = "flagged_v2_5.jsonl"
LOG_FILE        = "v2_5_processing.log"

DEFAULT_MODEL   = "gpt-4o"
MAX_RETRIES     = 2
THIN_THRESHOLD  = 50
API_DELAY       = 0.3
API_ERROR_DELAY = 10
MIN_DEF_LEN     = 20   # definition_short shorter than this → treated as missing

# ── ARTIFACT / QUALITY CHECKER ───────────────────────────────────────────────

_ARTIFACT_PATTERNS = [
    (r'\{\{|\}\}',            'wiki template markup {{ }}'),
    (r'\[\[|\]\]',            'wiki link markup [[ ]]'),
    (r'<ref',                 'reference tag <ref>'),
    (r'thumb\|',              'image thumb markup'),
    (r'\b\d+px\b',            'pixel dimension (e.g. 320px)'),
    (r'&[a-z]{2,8};',         'HTML entity (e.g. &amp;)'),
    (r'&#\d+;',               'numeric HTML entity'),
    (r'\(:\s*\w',             'plural notation  (: amnions)'),
    (r'\(\s*/[^)]{2,}/\s*\)', 'pronunciation guide (/ ... /)'),
    (r'\(\s*\)',              'empty parentheses ()'),
    (r'(?i)can refer to:',    'disambiguation phrase "can refer to:"'),
    (r'(?i)may refer to:',    'disambiguation phrase "may refer to:"'),
    (r'(?m)^\s*[*\-]\s',      'bullet list line'),
    (r'\[\d+\]',              'inline citation [1]'),
]

def _find_artifacts(text: str) -> list:
    return [desc for pattern, desc in _ARTIFACT_PATTERNS if re.search(pattern, text)]

def check_quality(keyword: str, result: dict) -> list:
    failures = []
    short = result.get('definition_short', '')
    raw   = result.get('definition_raw', '')
    kw8   = keyword.lower()[:8]

    if not short:
        failures.append('definition_short is empty')
    else:
        if not short.lower().startswith(kw8):
            kw_core = re.sub(r'\s*\(.*?\)', '', keyword).strip().lower()
            if kw_core not in short[:80].lower():
                failures.append(f'keyword "{keyword}" not found in first 80 chars of definition_short')
        if len(short) < 50:
            failures.append(f'definition_short too short ({len(short)} chars, need ≥50)')
        if len(short) > 450:
            failures.append(f'definition_short too long ({len(short)} chars, need ≤450)')
        for desc in _find_artifacts(short):
            failures.append(f'Artifact in definition_short: {desc}')

    if not raw:
        failures.append('definition_raw is empty')
    else:
        para_count = len([p for p in raw.split('\n\n') if p.strip()])
        if para_count < 3:
            failures.append(f'definition_raw needs ≥3 paragraphs (found {para_count})')
        if len(raw) < 300:
            failures.append(f'definition_raw too short ({len(raw)} chars, need ≥300)')
        for desc in _find_artifacts(raw):
            failures.append(f'Artifact in definition_raw: {desc}')

    return failures

# ── PROMPTS ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a biomedical education writer for Medhavi, a textbook platform for medical "
    "and life science students studying Cancer, Nanomedicine, Nanotechnology, and Biotechnology.\n\n"
    "Your task is to write clean, accurate, student-ready definitions for keyword entries. "
    "You write from your own expert knowledge, using the Wikipedia source text only as a "
    "factual reference — never as prose to copy or paraphrase directly.\n\n"
    "You must write a definition for EVERY keyword. "
    "For disambiguation pages, pick the most biomedical meaning and write for that. "
    "For non-biomedical terms, write a concise factual definition explaining what it is.\n\n"
    "The definitions appear in two places:\n"
    "  • definition_short — a popup tooltip when a student clicks a keyword\n"
    "  • definition_raw   — a full article modal when the student clicks \"Read more\"\n\n"
    "Always return valid JSON and nothing else."
)

def _build_user_prompt(keyword: str, source_text: str, categories: list, retry_note: str = "") -> str:
    cat_str = ", ".join(categories) if categories else "none"
    retry_block = (
        f"\n\n RETRY — your previous response failed these quality checks:\n"
        + "\n".join(f"  • {f}" for f in retry_note.split("\n") if f.strip())
        + "\nFix every item above in this response.\n"
    ) if retry_note else ""

    return f"""Process this keyword entry:{retry_block}

Keyword: {keyword}
Wikipedia source text:
{source_text}

Wikipedia categories: {cat_str}

---

STEP 1 — DISAMBIGUATION
If the source text begins with "X can refer to:" or a bullet list of meanings, pick the most
biomedical meaning and write for that only.

STEP 2 — CATEGORY (pick exactly one):
Oncology | Nanomedicine | Nanotechnology | Biotechnology | Molecular Biology | Cell Biology |
Biochemistry | Genetics | Immunology | Pharmacology | Anatomy | Chemistry | Diagnostics |
Biomedical Science

STEP 3 — definition_short (popup tooltip)
  • Exactly 2 sentences
  • Sentence 1: What it IS — open with "{keyword} is..." or "A/An/The {keyword} is..."
  • Sentence 2: Why it matters or key context
  • No wiki markup, pronunciation guides, plural notations, inline citations, or bullet lists

STEP 4 — definition_raw (full article)
  • Minimum 3 paragraphs separated by \\n\\n
  • Paragraph 1: identity/classification, Paragraph 2: mechanism or detail,
    Paragraph 3+: significance, applications, broader context
  • Enrich from your own knowledge — do not be limited by the Wikipedia source text
  • Same formatting rules as definition_short; no bullet points or numbered lists

Return ONLY valid JSON:
{{
  "category": "...",
  "definition_short": "...",
  "definition_raw": "..."
}}
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
            {"role": "user",   "content": prompt},
        ],
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw.strip())

# ── GENERATE MISSING DEFINITION ───────────────────────────────────────────────

def generate_definition(client, model, entry: dict, log) -> tuple:
    """Call the API to fill in a missing definition. Returns (updated_entry, needs_review)."""
    keyword = entry.get('keyword', '').strip()
    source  = entry.get('definition_raw', '')[:6000]   # raw Wikipedia text as anchor
    cats    = list(set(entry.get('categories', []) + entry.get('all_page_categories', [])))
    thin    = len(entry.get('definition_raw', '').split()) < THIN_THRESHOLD

    result       = None
    last_note    = ""
    retry_failed = False

    for attempt in range(1 + MAX_RETRIES):
        try:
            result = _call_openai(client, model, keyword, source, cats, last_note)
        except json.JSONDecodeError as e:
            last_note = f"Response was not valid JSON: {e}"
            log(f"    JSON error (attempt {attempt + 1}): {e}")
            time.sleep(API_ERROR_DELAY)
            continue
        except RateLimitError:
            log(f"    Rate limited — waiting 60s")
            time.sleep(60)
            continue
        except (APIError, BadRequestError) as e:
            log(f"    API error (attempt {attempt + 1}): {e}")
            time.sleep(API_ERROR_DELAY)
            continue

        failures = check_quality(keyword, result)
        if not failures:
            break
        last_note = "\n".join(failures)
        log(f"    Quality check failed (attempt {attempt + 1}): {failures}")
        time.sleep(API_DELAY)
    else:
        retry_failed = True
        log(f"    !! Retries exhausted for: {keyword}")

    if result is None:
        result = {"category": "Biomedical Science", "definition_short": "", "definition_raw": ""}
        retry_failed = True

    output = dict(entry)
    output['relevant']         = True
    output['category']         = result.get('category', entry.get('category', 'Biomedical Science'))
    output['definition_short'] = result.get('definition_short', '')
    output['definition_raw']   = result.get('definition_raw', '')
    if 'irrelevant_reason' in output:
        del output['irrelevant_reason']

    needs_review = thin or retry_failed
    if thin:
        output['_thin_source'] = True
    if retry_failed:
        output['_retry_exhausted'] = True
        output['_last_failures']   = last_note

    return output, needs_review

# ── RESUME SUPPORT ───────────────────────────────────────────────────────────

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
    parser = argparse.ArgumentParser(description="Fill missing definitions in keywords_clean.jsonl")
    parser.add_argument('--input',   default=DEFAULT_INPUT)
    parser.add_argument('--v3',      default=DEFAULT_V3,
                        help='keywords_v3.jsonl — source of clean definitions')
    parser.add_argument('--output',  default=DEFAULT_OUTPUT)
    parser.add_argument('--flagged', default=DEFAULT_FLAGGED)
    parser.add_argument('--model',   default=DEFAULT_MODEL)
    parser.add_argument('--resume',  action='store_true',
                        help='Skip already-written keywords in output file')
    args = parser.parse_args()

    input_path   = Path(args.input)
    output_path  = Path(args.output)
    flagged_path = Path(args.flagged)

    v3_path = Path(args.v3)

    for p in [input_path, v3_path]:
        if not p.exists():
            print(f"Error: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    # Load v3 as a lookup — these are the clean, GPT-rewritten definitions
    print(f"Loading v3 definitions from: {v3_path}")
    v3_lookup = {}
    with open(v3_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if len(e.get('definition_short', '').strip()) >= MIN_DEF_LEN:
                    v3_lookup[e['keyword']] = e
            except Exception:
                pass
    print(f"  v3 entries with usable definitions: {len(v3_lookup)}")

    client = OpenAI()

    already_done = load_done_keywords(output_path) if args.resume else set()
    if already_done:
        print(f"Resuming — {len(already_done)} entries already in output")

    with open(input_path, encoding='utf-8') as f:
        total = sum(1 for line in f if line.strip())

    log_file = open(LOG_FILE, 'a', encoding='utf-8')
    def log(msg):
        print(msg)
        log_file.write(msg + '\n')
        log_file.flush()

    write_mode = 'a' if args.resume else 'w'
    out_f     = open(output_path,  write_mode, encoding='utf-8')
    flagged_f = open(flagged_path, write_mode, encoding='utf-8')

    passthrough = skipped = generated = flagged = errors = 0

    try:
        with open(input_path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
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

                v3_entry = v3_lookup.get(keyword)

                if v3_entry:
                    # Use v3's clean definition — best quality
                    output = dict(v3_entry)
                    output['relevant'] = True
                    if 'irrelevant_reason' in output:
                        del output['irrelevant_reason']
                    out_f.write(json.dumps(output, ensure_ascii=False) + '\n')
                    out_f.flush()
                    passthrough += 1
                else:
                    # Not in v3 or v3 had no definition — generate one
                    log(f"[{i}/{total}] Generating: {keyword}")
                    try:
                        output, needs_review = generate_definition(client, args.model, entry, log)
                        out_f.write(json.dumps(output, ensure_ascii=False) + '\n')
                        out_f.flush()

                        if needs_review:
                            flagged_f.write(json.dumps(output, ensure_ascii=False) + '\n')
                            flagged_f.flush()
                            flagged += 1
                            tag = []
                            if output.get('_thin_source'):     tag.append('thin source')
                            if output.get('_retry_exhausted'): tag.append('retries exhausted')
                            log(f"  → flagged ({', '.join(tag)})")

                        generated += 1
                    except Exception as e:
                        log(f"  !! Unhandled error on '{keyword}': {e}")
                        errors += 1

                    time.sleep(API_DELAY)

    finally:
        out_f.close()
        flagged_f.close()
        summary = (
            f"\n{'='*60}\n"
            f"Done.\n"
            f"  Passed through (from v3): {passthrough}\n"
            f"  Generated (not in v3):    {generated}\n"
            f"  Skipped (resume):         {skipped}\n"
            f"  Flagged for review:       {flagged}  -> {flagged_path}\n"
            f"  Errors:                   {errors}\n"
            f"{'='*60}"
        )
        log(summary)
        log_file.close()

if __name__ == '__main__':
    main()
