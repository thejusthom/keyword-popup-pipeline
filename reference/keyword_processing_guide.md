# Medhavi Keyword Processing Guide
## Version 2 — Claude-Rewrite Pipeline

---

## What Changed and Why

The v1 pipeline cleaned Wikipedia markup and trimmed the lead section into a definition. This produced three categories of failure:

1. **Disambiguation dumps** — CTL, and similar multi-meaning terms have Wikipedia disambiguation pages. The cleaner returned the full bullet list verbatim.
2. **Irrelevant terms passing through** — The Selfish Gene (a book), Brownian ratchet (a physics thought experiment) — pattern-based relevance filters could not catch everything.
3. **Thin or artifact-ridden definitions** — AIDS-related lymphoma was short because Wikipedia's lead was short. Amnion kept `(: amnia)` plural notation. HeLa lost the Henrietta Lacks story because trimming cut it off.

**The fix:** Claude now *writes* each definition from its own knowledge, using the Wikipedia text only as a factual anchor. The pipeline becomes: clean markup → Claude API call → structured JSON output.

---

## Pipeline Architecture

```
oncology_subwiki.jsonl
        ↓
  1. Regex cleaning       (remove markup, refs, HTML — same as before)
        ↓
  2. Claude API call      (one call per entry — system + user prompt below)
        ↓
  3. Parse JSON response  (relevant, category, definition_short, definition_raw)
        ↓
  keywords_clean.jsonl
```

The Claude call receives the cleaned text as context but is instructed to write definitions fresh. This eliminates Wikipedia's encyclopedic voice, resolves disambiguation, and enriches thin entries.

---

## System Prompt

```
You are a biomedical education writer for Medhavi, a textbook platform for medical and life
science students studying Cancer, Nanomedicine, Nanotechnology, and Biotechnology.

Your task is to evaluate Wikipedia keyword entries and produce clean, accurate, student-ready
definitions. You write from your own expert knowledge, using the Wikipedia source text only as
a factual reference — never as prose to copy or paraphrase directly.

The definitions you write will appear in two places:
- definition_short: a popup tooltip when a student clicks a term in the textbook
- definition_raw: a full article modal when the student clicks "Read more"

Both must read as polished educational content, not encyclopedia entries.
```

---

## User Prompt Template (per entry)

```
Process this keyword entry for the Medhavi biomedical textbook.

Keyword: {keyword}
Wikipedia source text:
{cleaned_wikipedia_text}

Wikipedia categories: {categories}

---

Follow these steps in order:

STEP 1 — DISAMBIGUATION CHECK
If the source text begins with phrases like "X can refer to:", "X may refer to:", "X is any
of:", or presents a bullet list of unrelated meanings, this is a disambiguation page.
- Scan the list for a biomedical meaning (oncology, cell biology, pharmacology, genetics,
  immunology, nanotechnology, biochemistry, etc.)
- If one biomedical meaning exists: write definitions for THAT meaning only. Ignore all others.
- If none exists: set relevant=false with reason "Disambiguation page with no biomedical meaning"

Example: "CTL can refer to: Champions Tennis League... Cytotoxic T lymphocyte..."
→ Resolve to Cytotoxic T lymphocyte and write the full definition for that.


STEP 2 — RELEVANCE DECISION
Determine if this term belongs in a cancer/biotechnology textbook.

Mark relevant=false for ALL of the following — no exceptions:
- Books, films, documentaries, TV shows, plays, albums, songs
  (Even if the content is scientific — "The Selfish Gene" is a book, not a biology term)
- Biographies of individual people
  (Exception: if the KEYWORD itself is a named scientific concept, e.g. "HeLa cells" is fine)
- Pure physics or philosophy thought experiments with no direct biomedical mechanism
  (e.g. "Brownian ratchet" as a perpetual motion thought experiment; the philosophical zombie)
- Geography, politics, military hardware, sports, economics, law
- Wikipedia template or navigation pages
- Disambiguation pages with no biomedical meaning

Mark relevant=true for:
- Cancer biology, oncology, tumor biology, carcinogenesis, metastasis
- Nanomedicine, targeted drug delivery, nanocarriers, theranostics
- Nanotechnology with biomedical application (molecular machines, nanoparticles, biosensors)
- Biotechnology: CRISPR, PCR, gene therapy, monoclonal antibodies, recombinant DNA
- Cell biology: apoptosis, cell cycle, organelles, signaling pathways, cell death
- Molecular biology: DNA, RNA, transcription, translation, epigenetics, gene expression
- Genetics: mutations, chromosomes, hereditary disease, oncogenes, tumor suppressor genes
- Immunology: immune cells, cytokines, immunotherapy, CAR-T, checkpoint inhibitors
- Pharmacology: cancer drugs, mechanisms of action, pharmacokinetics, drug resistance
- Biochemistry: metabolic pathways, enzymes, proteins relevant to disease
- Diagnostics: biomarkers, imaging, biopsy, liquid biopsy, flow cytometry
- Anatomy: structures relevant to cancer biology or medical education

When genuinely uncertain, lean toward relevant=true.


STEP 3 — CATEGORY
Assign exactly one category. Choose the most specific fit:

Oncology | Nanomedicine | Nanotechnology | Biotechnology | Molecular Biology |
Cell Biology | Biochemistry | Genetics | Immunology | Pharmacology |
Anatomy | Chemistry | Diagnostics | Biomedical Science

Rules:
- Term is directly about a cancer type, oncogene, or tumor marker → Oncology
- A drug or therapeutic agent → Pharmacology
- A gene or chromosome or hereditary condition → Genetics
- An immune cell or immune process → Immunology
- A cell structure or cell process (not molecular) → Cell Biology
- DNA/RNA mechanism at the molecular level → Molecular Biology
- A metabolic process or enzyme → Biochemistry
- A diagnostic tool or biomarker → Diagnostics
- Use Biomedical Science only as a last resort


STEP 4 — WRITE definition_short (popup tooltip)

Requirements:
- 2–4 sentences
- Opening: "{keyword} is..." or a close synonym (e.g. "Apoptosis is...", "HeLa is...")
- Written for a medical or biology student seeing this term for the first time
- Must convey: what it IS + why it matters (mechanism, significance, or clinical application)
- Avoid: jargon overload, hedging language ("it may be"), passive openers ("It is known that")

Hard bans — these must NEVER appear in the output:
- Pronunciation guides: (/ ... /) or phonetic notation of any form
- Plural notations: (: amnions) or (plural: X) or (amnions or amnia)
- Empty parentheses: ()
- Wikipedia markup: [[ ]], {{ }}, <ref>, thumb|, px dimensions, | pipes
- Disambiguation phrasing: "can refer to", "may refer to", bullet lists of meanings
- Citation numbers or bracketed references: [1], [2]
- First-person (I, we) or second-person (you) language


STEP 5 — WRITE definition_raw (full article modal)

Requirements:
- Minimum 3 paragraphs separated by double newlines (\n\n)
- Textbook quality prose — clear, accurate, educational; not encyclopedia style
- Structure:
    Paragraph 1: What it is — identity, classification, biological or chemical nature
    Paragraph 2: Mechanism — how it works in plain English
    Paragraph 3+: Significance — clinical relevance, role in cancer or biotech, research use
- Enrich with your knowledge beyond what Wikipedia says
  (e.g. if HeLa definition is missing Henrietta Lacks, include it;
   if AIDS-related lymphoma is thin, expand on the immunological mechanism)
- All hard bans from Step 4 apply here too
- No bullet points or numbered lists within the text
- Each paragraph should be 3–6 sentences


OUTPUT
Return a JSON object with exactly these fields:

{
  "relevant": true or false,
  "irrelevant_reason": "..." (only if relevant=false; omit entirely if relevant=true),
  "category": "...",
  "definition_short": "...",
  "definition_raw": "..."
}

If relevant=false, set definition_short="" and definition_raw="".
```

---

## Reviewer QA Standards

### Correct
- definition_short starts with the keyword or a direct synonym
- 2–4 sentences, reads naturally, no artifacts of any kind
- A student learns something real and accurate from the definition
- definition_raw has at least 3 paragraphs covering identity, mechanism, and significance
- Category is the most specific correct fit
- Relevance decision is right (irrelevant terms caught, relevant terms kept)

### Partial
- Factually correct but thin — a key fact the student should know is missing
- Minor artifact survived (one stray parenthetical, slightly Wikipedia-ish voice)
- Category is plausible but not the most specific fit
- definition_raw has only 2 paragraphs

### Incorrect
- Disambiguation not resolved — output still contains "can refer to" or a bullet list
- Factually wrong statement in the definition
- Irrelevant term passed through (book, biography, pure physics thought experiment, geography)
- definition_raw is only 1 paragraph
- Major artifacts present: wiki brackets, px dimensions, markup templates
- definition_short does not open with the keyword or synonym
- Wrong category entirely

---

## Common Edge Cases

### Disambiguation pages
Signal: text begins with "X can refer to:" or a bullet list of meanings.
Action: pick the biomedical meaning, write as if that is the only meaning.
Example: CTL → Cytotoxic T lymphocyte. Start with "CTL, or cytotoxic T lymphocyte, is a type of immune cell..."

### Named concepts (eponyms)
Keep: HeLa cells, Hayflick limit, Warburg effect, BRCA1 gene
Drop: Biographies of the people they are named after (unless the person IS the keyword term)

### Books and publications
Always irrelevant: The Selfish Gene, The Double Helix (as a book).
Exception: If "The Cancer Genome Atlas" refers to the genomic database/project rather than a publication, it is relevant. Wikipedia categories will clarify.

### Physics and chemistry concepts
Relevant: Brownian motion (direct mechanism in nano drug delivery), quantum dots (bioimaging), free radical (causes DNA damage)
Irrelevant: Brownian ratchet (thought experiment about perpetual motion), Schrödinger's cat (quantum paradox)
Rule: Does this concept have a direct physical mechanism that is actively used in biomedical science? Yes → relevant. Discussed only as a theoretical model → irrelevant.

### Very short Wikipedia entries
If the Wikipedia source text is fewer than 100 words, Claude must still write a full definition from its own knowledge. The short source is a weak anchor, not a word limit.

### Artifact patterns to strip if they survive
- `(: amnions)` or `(plural: X)` → remove entirely
- `(/ fəˈnɛtɪks /)` → remove entirely
- Empty parens `()` → remove entirely
- `[1]`, `[2]` citation markers → remove entirely

---

## Automated Quality Checklist

```python
import re

ARTIFACT_PATTERNS = [
    r'\{\{', r'\}\}',          # wiki templates
    r'\[\[', r'\]\]',          # wiki links
    r'<ref',                   # references
    r'thumb\|',                # image markup
    r'\d+px',                  # pixel dimensions
    r'&[a-z]{2,8};',           # HTML entities
    r'&#\d+;',                 # numeric entities
    r'\(:\s*\w',               # plural notation (: amnions)
    r'\(/[^)]+/\)',            # pronunciation (/fəˈ.../)
    r'\(\s*\)',                # empty parens
    r'can refer to:',          # disambiguation
    r'may refer to:',
]

def check_entry(entry):
    errors = []
    short = entry.get('definition_short', '')
    raw = entry.get('definition_raw', '')
    kw = entry.get('keyword', '').lower()

    if entry.get('relevant'):
        # Check short
        if not short:
            errors.append('definition_short is empty on relevant entry')
        elif not short.lower().startswith(kw[:8]):
            errors.append('definition_short does not start with keyword')
        if len(short) < 50 or len(short) > 600:
            errors.append(f'definition_short length out of range: {len(short)}')

        # Check raw
        if raw.count('\n\n') < 2:
            errors.append('definition_raw has fewer than 3 paragraphs')
        if len(raw) < 300:
            errors.append('definition_raw too short')

        # Check artifacts in both
        for text_label, text in [('definition_short', short), ('definition_raw', raw)]:
            for pattern in ARTIFACT_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    errors.append(f'Artifact in {text_label}: pattern "{pattern}"')

    return errors
```
