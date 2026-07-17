    #!/usr/bin/env python3
"""
process_keywords_v3.py  —  OpenAI edition

Re-processes keywords_clean.jsonl using the OpenAI API.

What this does:
  - GPT writes definitions from its own knowledge (Wikipedia text = anchor only)
  - Resolves disambiguation pages to the biomedical meaning
  - Strict relevance: books, biographies, thought experiments are rejected
  - Auto-retries entries that fail the artifact/quality checker (up to MAX_RETRIES)
  - Flags thin-source entries (<50 words) for human spot-check
  - Supports --resume to continue an interrupted run

Usage:
    export OPENAI_API_KEY=sk-...
    python process_keywords_v3.py                           # full run, gpt-4o
    python process_keywords_v3.py --model gpt-4o-mini       # cheaper (~$9 total)
    python process_keywords_v3.py --resume                  # continue interrupted run
    python process_keywords_v3.py --input keywords_clean.jsonl --output keywords_v3.jsonl
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from openai import OpenAI, RateLimitError, APIError, BadRequestError

# ── CONFIG ───────────────────────────────────────────────────────────────────

DEFAULT_INPUT   = "keywords_clean.jsonl"
DEFAULT_OUTPUT  = "keywords_v3.jsonl"
DEFAULT_FLAGGED = "flagged_for_review.jsonl"
LOG_FILE        = "v3_processing.log"

DEFAULT_MODEL   = "gpt-4o"       # swap to gpt-4o-mini for ~$9 total (~$155 for gpt-4o)
MAX_RETRIES     = 2              # quality-check retries per entry
THIN_THRESHOLD  = 50             # words; below this → flagged for human review
API_DELAY       = 0.3            # seconds between calls
API_ERROR_DELAY = 10             # seconds to wait after an API error

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
    """Returns list of failure strings, or [] if entry passes."""
    if not result.get('relevant'):
        return []
    failures = []
    short = result.get('definition_short', '')
    raw   = result.get('definition_raw', '')
    kw8   = keyword.lower()[:8]

    if not short:
        failures.append('definition_short is empty')
    else:
        if not short.lower().startswith(kw8):
            # also accept "A/An/The <keyword>" openings
            kw_core = re.sub(r'\s*\(.*?\)', '', keyword).strip().lower()
            if kw_core not in short[:80].lower():
                failures.append(f'keyword "{keyword}" not found in first 80 chars of definition_short')
        if len(short) < 50:
            failures.append(f'definition_short too short ({len(short)} chars, need ≥50)')
        if len(short) > 450:
            failures.append(f'definition_short too long ({len(short)} chars, need ≤450 — exactly 2 sentences)')
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
    "Your task is to evaluate Wikipedia keyword entries and produce clean, accurate, "
    "student-ready definitions. You write from your own expert knowledge, using the Wikipedia "
    "source text only as a factual reference — never as prose to copy or paraphrase directly.\n\n"
    "The definitions appear in two places:\n"
    "  • definition_short — a popup tooltip when a student clicks a keyword\n"
    "  • definition_raw   — a full article modal when the student clicks \"Read more\"\n\n"
    "Both must read as polished educational content, not encyclopedia entries.\n"
    "Always return valid JSON and nothing else."
)

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
  "definition_raw": "Cytotoxic T lymphocytes (CTLs), also called CD8+ T cells, are a specialized subset of T lymphocytes whose primary function is to seek out and destroy cells displaying abnormal antigens — including virus-infected cells and cancer cells. They are key effector cells of the adaptive immune system and are essential to the body's immunosurveillance against tumors.\\n\\nCTLs recognize their targets through the T cell receptor (TCR), which binds to antigen fragments presented on MHC class I molecules found on virtually all nucleated cells. Upon recognition, the CTL forms an immunological synapse with the target and releases cytotoxic granules containing perforin and granzymes. Perforin punches pores in the target cell membrane while granzymes enter to activate caspase-mediated apoptosis, killing the cell within minutes without harming surrounding tissue.\\n\\nIn oncology, CTLs are a critical component of anti-tumor immunity. Many cancers evade immune destruction by downregulating MHC class I expression or expressing PD-L1, a ligand that suppresses CTL activity. Immune checkpoint inhibitors — drugs that block PD-1, PD-L1, or CTLA-4 — reactivate exhausted CTLs to attack tumors. CAR-T cell therapy goes further, engineering a patient's own CTLs with chimeric antigen receptors designed to target tumor-specific surface markers directly."
}

--- Example 2: Irrelevant term (book) correctly rejected ---
Keyword: The Selfish Gene
Wikipedia source text:
The Selfish Gene is a 1976 book by ethologist Richard Dawkins that promotes the gene-centered view of evolution.
Wikipedia categories: [Books by Richard Dawkins, Popular science books]

Output:
{
  "relevant": false,
  "irrelevant_reason": "This is a popular science book by Richard Dawkins, not a biomedical term or concept.",
  "category": "Molecular Biology",
  "definition_short": "",
  "definition_raw": ""
}

--- Example 3: Thin Wikipedia entry enriched ---
Keyword: AIDS-related lymphoma
Wikipedia source text:
AIDS-related lymphoma describes lymphomas occurring in patients with acquired immunodeficiency syndrome (AIDS). A lymphoma is a type of cancer arising from lymphoid cells.
Wikipedia categories: [Oncology, HIV/AIDS]

Output:
{
  "relevant": true,
  "category": "Oncology",
  "definition_short": "AIDS-related lymphoma is a malignancy of the lymphatic system that arises in HIV/AIDS patients due to severe immunosuppression-driven B-cell transformation. It is classified as an AIDS-defining illness and is treated with combined antiretroviral therapy and aggressive chemotherapy regimens.",
  "definition_raw": "AIDS-related lymphoma (ARL) is a group of malignant cancers of the lymphatic system that arise in the setting of HIV infection and its resulting immunodeficiency. They are classified as AIDS-defining malignancies — their occurrence in an HIV-positive individual signals progression to full AIDS. The most common subtypes are diffuse large B-cell lymphoma (DLBCL) and Burkitt lymphoma, both of which are aggressive B-cell malignancies.\\n\\nThe pathogenesis of ARL is closely tied to the immune collapse caused by HIV. As CD4+ T cell counts fall, immune surveillance against oncogenic viruses deteriorates. Epstein-Barr virus (EBV) and human herpesvirus 8 (HHV-8) play direct roles in driving lymphoid transformation in many ARL cases. Chronic B-cell stimulation combined with the loss of regulatory T cell function creates conditions for chromosomal translocations — particularly those involving the MYC oncogene — that drive uncontrolled B-cell proliferation.\\n\\nClinically, ARL tends to present at advanced stage with frequent extranodal involvement, including the central nervous system, bone marrow, and gastrointestinal tract. Treatment combines antiretroviral therapy (ART) to restore immune function with systemic chemotherapy such as R-CHOP or CODOX-M/IVAC. The introduction of effective ART has substantially improved ARL outcomes, though prognosis remains guarded compared to lymphoma in immunocompetent individuals."
}

--- Example 4: Formatting artifacts stripped ---
Keyword: Amnion
Wikipedia source text:
The amnion (: amnions or amnia) is a membrane that closely covers human and various other embryos when they first form. It fills with amniotic fluid, which causes the amnion to expand and become the amniotic sac that provides a protective environment for the developing embryo.
Wikipedia categories: [Embryology, Cell Biology]

Output:
{
  "relevant": true,
  "category": "Anatomy",
  "definition_short": "The amnion is a thin membrane enclosing the developing embryo within amniotic fluid, providing mechanical cushioning and a sterile environment throughout pregnancy. It is clinically important in obstetrics and prenatal diagnostics, including amniocentesis for chromosomal screening.",
  "definition_raw": "The amnion is the innermost of the fetal membranes, forming a fluid-filled sac that directly envelops the developing embryo or fetus throughout pregnancy in amniotic vertebrates. It is a thin, translucent, avascular membrane composed primarily of epithelial cells on a collagen-rich basement membrane. The enclosed space, called the amniotic cavity, fills with amniotic fluid produced initially by the amnion itself and later largely by fetal urine.\\n\\nAmniotic fluid serves multiple protective functions: it cushions the fetus against mechanical trauma, maintains a constant temperature, permits fetal movement essential for musculoskeletal development, and provides a sterile barrier against infection. The volume is tightly regulated by fetal swallowing and urination. Abnormalities such as polyhydramnios or oligohydramnios are associated with fetal abnormalities and adverse pregnancy outcomes.\\n\\nClinically, the amnion and its fluid are important in prenatal diagnosis. Amniocentesis allows analysis of fetal cells for chromosomal conditions such as Down syndrome and hereditary diseases. In regenerative medicine, amniotic membrane tissue has attracted research interest for its anti-inflammatory and anti-angiogenic properties, with applications in wound healing and surgical reconstruction."
}

=== END EXAMPLES ===
"""

def _build_user_prompt(keyword: str, source_text: str, categories: list, retry_note: str = "") -> str:
    cat_str = ", ".join(categories) if categories else "none"
    retry_block = (
        f"\n\n RETRY — your previous response failed these quality checks:\n"
        + "\n".join(f"  • {f}" for f in retry_note.split("\n") if f.strip())
        + "\nFix every item above in this response.\n"
    ) if retry_note else ""

    return f"""{_FEW_SHOT}
---

Now process this entry:{retry_block}

Keyword: {keyword}
Wikipedia source text:
{source_text}

Wikipedia categories: {cat_str}

---

STEP 1 — DISAMBIGUATION CHECK
If the source text begins with "X can refer to:", "X may refer to:", or a bullet list of
unrelated meanings, scan for a biomedical meaning and write definitions for THAT meaning only.
If no biomedical meaning exists → relevant=false.

STEP 2 — RELEVANCE
Mark relevant=false (no exceptions) for:
  • Books, films, TV shows, plays, albums, songs — even if scientifically themed
  • Biographies of individual people
    (Exception: named scientific concepts such as "HeLa cells" are fine)
  • Pure physics/philosophy thought experiments with no direct biomedical mechanism
  • Geography, politics, military, sports, economics, law
  • Wikipedia template or navigation pages

Mark relevant=true for:
  Cancer biology · Nanomedicine · Nanotechnology (biomedical application) · Biotechnology ·
  Cell biology · Molecular biology · Genetics · Immunology · Pharmacology · Biochemistry ·
  Diagnostics · Anatomy · Chemistry (when linked to biology or medicine)

When uncertain → lean toward relevant=true.

STEP 3 — CATEGORY (pick exactly one):
Oncology | Nanomedicine | Nanotechnology | Biotechnology | Molecular Biology | Cell Biology |
Biochemistry | Genetics | Immunology | Pharmacology | Anatomy | Chemistry | Diagnostics |
Biomedical Science

STEP 4 — definition_short (popup tooltip)
  • Exactly 2 sentences — no more, no fewer
  • Target length: 120–200 characters per sentence (content complexity may vary slightly, but keep it tight)
  • Sentence 1: What it IS — open with "{keyword} is..." or "A/An/The {keyword} is..."
  • Sentence 2: Why it matters — one key clinical or biological significance
  • NEVER include: (/ phonetics /), (: plural), (), wiki markup, "can refer to",
    inline citations [1], bullet lists

STEP 5 — definition_raw (full article)
  • Minimum 3 paragraphs separated by \\n\\n
  • Structure: paragraph 1 = identity/classification, paragraph 2 = mechanism,
    paragraph 3+ = clinical/research significance in cancer or biotech
  • Enrich from your own knowledge — do NOT be limited by the Wikipedia source text
  • Same hard bans as definition_short; no bullet points or numbered lists

Return ONLY valid JSON — no markdown fences, no commentary:
{{
  "relevant": true or false,
  "irrelevant_reason": "..." (only if relevant=false; omit key entirely if relevant=true),
  "category": "...",
  "definition_short": "...",
  "definition_raw": "..."
}}
If relevant=false → definition_short="" and definition_raw="".
"""

# ── API CALL ─────────────────────────────────────────────────────────────────

def _call_openai(client: OpenAI, model: str,
                 keyword: str, source: str, cats: list, retry_note: str = "") -> dict:
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
    # Strip markdown fences if model wraps output
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw.strip())

# ── MAIN PROCESSING ──────────────────────────────────────────────────────────

def process_entry(client: OpenAI, model: str, entry: dict, log) -> tuple:
    """Returns (output_entry, needs_human_review)."""
    keyword  = entry.get('keyword', '').strip()
    source   = entry.get('definition_raw', '')[:6000]   # cap to avoid request size errors
    cats     = list(set(entry.get('categories', []) + entry.get('all_page_categories', [])))
    thin     = len(entry.get('definition_raw', '').split()) < THIN_THRESHOLD

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
        result = {
            "relevant": False,
            "irrelevant_reason": "Processing failed — all API attempts errored",
            "category": "Biomedical Science",
            "definition_short": "",
            "definition_raw": "",
        }
        retry_failed = True

    output = {k: v for k, v in entry.items()
              if k not in ('definition_short', 'definition_raw', 'category', 'relevant', 'irrelevant_reason')}
    output['keyword']          = keyword
    output['relevant']         = result.get('relevant', False)
    output['category']         = result.get('category', entry.get('category', 'Biomedical Science'))
    output['definition_short'] = result.get('definition_short', '')
    output['definition_raw']   = result.get('definition_raw', '')
    if not result.get('relevant'):
        output['irrelevant_reason'] = result.get('irrelevant_reason', '')

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
    parser = argparse.ArgumentParser(description="Reprocess keyword JSONL with OpenAI API")
    parser.add_argument('--input',   default=DEFAULT_INPUT)
    parser.add_argument('--output',  default=DEFAULT_OUTPUT)
    parser.add_argument('--flagged', default=DEFAULT_FLAGGED)
    parser.add_argument('--model',   default=DEFAULT_MODEL,
                        help='OpenAI model (gpt-4o ~$155, gpt-4o-mini ~$9)')
    parser.add_argument('--resume',  action='store_true',
                        help='Skip already-processed keywords in output file')
    args = parser.parse_args()

    input_path   = Path(args.input)
    output_path  = Path(args.output)
    flagged_path = Path(args.flagged)

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    client = OpenAI()   # reads OPENAI_API_KEY from environment

    already_done = load_done_keywords(output_path) if args.resume else set()
    if already_done:
        print(f"Resuming — {len(already_done)} entries already processed")

    with open(input_path, encoding='utf-8') as f:
        total = sum(1 for line in f if line.strip())

    log_file = open(LOG_FILE, 'a', encoding='utf-8')

    def log(msg: str):
        print(msg)
        log_file.write(msg + '\n')
        log_file.flush()

    write_mode = 'a' if args.resume else 'w'
    out_f     = open(output_path,  write_mode, encoding='utf-8')
    flagged_f = open(flagged_path, write_mode, encoding='utf-8')

    processed = skipped = flagged = errors = 0

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

                log(f"[{i}/{total}] {keyword}")

                try:
                    output_entry, needs_review = process_entry(client, args.model, entry, log)

                    out_f.write(json.dumps(output_entry, ensure_ascii=False) + '\n')
                    out_f.flush()

                    if needs_review:
                        flagged_f.write(json.dumps(output_entry, ensure_ascii=False) + '\n')
                        flagged_f.flush()
                        flagged += 1
                        tag = []
                        if output_entry.get('_thin_source'):    tag.append('thin source')
                        if output_entry.get('_retry_exhausted'): tag.append('retries exhausted')
                        log(f"  → flagged ({', '.join(tag)})")

                    processed += 1

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
            f"  Processed : {processed}\n"
            f"  Skipped   : {skipped}  (already done)\n"
            f"  Flagged   : {flagged}  → {flagged_path}\n"
            f"  Errors    : {errors}\n"
            f"{'='*60}"
        )
        log(summary)
        log_file.close()

if __name__ == '__main__':
    main()
