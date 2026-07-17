# Claude Code Prompt — Keyword Popup Pipeline App

Paste the "Prompt" section below into Claude Code to build this as a standalone, reusable CLI app for a textbook on any subject. Read the "Background" section first so you understand what you're asking it to build.

---

## Background (read this first)

This is a system for building a keyword popup feature for a textbook: hover over a term the textbook uses, get a short tooltip definition; click it, get a full article. Every entry in that popup system comes from one of two pipelines:

- **Pipeline A (Wikipedia)** — the bulk of the content. Stream the full English Wikipedia XML dump, keep only pages relevant to the subject, clean the text, and have an LLM rewrite each definition properly.
- **Pipeline B (Abbreviations)** — a smaller supplementary set. Scan the textbook's own source files for abbreviations and shorthand that either don't have their own clean Wikipedia article or aren't well covered by Pipeline A, and have an LLM write definitions for those, using how they're actually used in the textbook as context.

Both pipelines produce the same JSONL format and get merged into one file, which is then filtered and uploaded to a Supabase database (and also exported as a `keywords.json` file that a Next.js app reads at build time to know which words in the textbook to make clickable).

Every subject-specific piece — the Wikipedia category list, the category labels, the relevance rules, the LLM prompts' domain name — lives in one config file. Swapping the config is what points the same pipeline at a different textbook.

**Getting the config filled in for a new subject doesn't have to start from a blank file.** The textbook's own MDX source is needed later anyway (Stage 4 scans it for abbreviations), so the app should offer a `config-init` stage that scans the textbook *first* — chapter files, `meta.json` files, everything — and makes one LLM call to draft the whole config file plus the Wikipedia category list, before the user writes a single line by hand. See Stage 0 below.

**The chain, with the actual script names it's implemented with:**
```
textbook's own MDX/Markdown source files
   → config-init                   (scan textbook, LLM drafts config + categories.txt)
   → config.yaml, categories.txt   ← draft, human-reviewed before use
                    ↓
enwiki dump
   → subwiki.py                    (stream + category filter)
   → process_keywords_v2.py        (clean markup, assign category, rule-based short definition)
   → process_keywords_v3.py        (LLM rewrites every definition properly)
   → keywords_v3.jsonl             ← usable Pipeline A output
                    +
the textbook's own MDX/Markdown source files
   → build_all_abbrevs.py          (scan for abbreviations)
   → enrich_abbrevs.py             (LLM writes definitions with textbook context)
   → keywords_abbrevs_enriched.jsonl
                    ↓
   → merge_keywords.py             (Pipeline A wins on overlap)
   → keywords_combined.jsonl
   → upload_to_supabase.py         (filter + upload + export keywords.json)
```

There is also an *optional* extra quality-assurance layer — running every definition through three different LLMs for a second opinion, then reassembling an even more polished file (`keywords_v5.jsonl`). Not required for a working pipeline. If you want Claude Code to build that too, say so explicitly; otherwise `keywords_v3.jsonl`-equivalent output is the finish line for Pipeline A.

---

## What You'll Need Before Running the App

Tell Claude Code to remind the user of these prerequisites in the README it generates:

- Python 3.10+
- A Wikipedia XML dump — download from `https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles.xml.bz2` (large file, ~20GB compressed; can run on a laptop but will take hours, or on a cluster in under an hour)
- An OpenAI (or Anthropic) API key — definitions are LLM-written, this is not optional
- A Supabase project with a database — free tier is fine to start
- The textbook's own MDX/Markdown source files — needed twice: first to draft `config.yaml` (Stage 0), then again for the abbreviation scan (Stage 4)
- No account needed for Gene Ontology — its public API is unauthenticated

**Starting point for a brand-new subject:** the user doesn't need to write `config.yaml` by hand. Point them at Stage 0 (`config-init`) first — paste or point it at the textbook's docs folder, run it, review the draft it produces, then proceed to Stage 1.

---

## Prompt

Build a CLI app called `keyword-pipeline` that generates keyword popup data for any MDX/Markdown textbook, by combining a Wikipedia-derived keyword set with a textbook-scanned abbreviation set. This is a reimplementation of an existing working pipeline (built for an oncology textbook) — generalize it so any subject can configure it via one config file instead of editing Python.

### Subcommands

Every stage reads from and writes to a file, so the whole thing is resumable at any point — if stage 4 crashes, you shouldn't have to redo stages 1–3.

```
keyword-pipeline config-init     # Stage 0: scan textbook, LLM drafts config.yaml + categories.txt
keyword-pipeline wiki-extract    # Stage 1: stream Wikipedia XML dump → raw JSONL
keyword-pipeline wiki-clean      # Stage 2: rule-based markup cleaning, no LLM cost
keyword-pipeline wiki-rewrite    # Stage 3: LLM rewrites every definition properly
keyword-pipeline scan            # Stage 4: scan textbook MDX files for abbreviations
keyword-pipeline enrich          # Stage 5: LLM writes abbreviation definitions
keyword-pipeline merge           # Stage 6: merge Wikipedia set + abbreviation set
keyword-pipeline upload          # Stage 7: filter, export keywords.json, upload to Supabase
keyword-pipeline run-all         # Run stages 1–7 end to end (config.yaml must already exist)
```

`config-init` is the recommended starting point for a brand-new subject — it's the only stage that doesn't require `config.yaml` to already exist. Every other stage reads from it.

---

### Stage 0 — `config-init`

**Reference implementation:** `00_config_generator/generate_config.py` in the reference folder — a working, tested version of this stage already exists; adapt it rather than designing the scan/prompt logic from scratch.

**Why this exists:** every other stage depends on `config.yaml` being filled in correctly, and the textbook's own MDX source — which the user has to provide anyway for Stage 4 — already contains most of the signal needed to draft it: the subject domain, the chapter/topic structure (from folder names and `meta.json` files), and a realistic list of abbreviations actually used in this specific textbook (rather than guessed generically). Scanning that first turns config setup from "the user writes 200+ lines of Python/YAML by hand, consulting Wikipedia and an LLM chat window on the side" into "the user reviews and edits an LLM-generated draft."

**Input:** the textbook's MDX/MD source directory, scanned recursively (same directory Stage 4 uses). Expect the layout to look like a typical docs platform: chapter folders, each containing numbered `.mdx` files and often a `meta.json` (e.g. `Chapter1/6_Tissue_Organization_And_Organ_Systems.mdx`, `Chapter1/meta.json`) — don't assume a flat file list.

**What it does, step by step:**
1. Recursively find every `.mdx`/`.md` file and every `meta.json`. Group files by parent folder (the chapter).
2. For each file: strip JSX/HTML tags, code fences, inline code, and `import`/`export` lines; extract a title (first `# Heading`, or derive one from the filename by stripping a leading `\d+_` prefix and replacing underscores with spaces); keep a content sample (a few hundred characters is enough — this is a structural scan, not a full-text ingestion).
3. Parse every `meta.json` found and keep its contents keyed by chapter folder — these files often already contain a clean chapter title and ordering that's more reliable than filename-guessing.
4. Run the same abbreviation-frequency regex Stage 4 uses (`\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b`) across the whole corpus and keep the top ~80 most frequent as concrete evidence for the LLM to reason about, instead of it inventing plausible-sounding abbreviations from the subject name alone.
5. Format all of the above into a compact text profile (chapter list, sample titles/content per chapter, meta.json snippets, top abbreviations) and send it to the LLM with a system prompt instructing it to return one JSON object containing every field `config.yaml` needs: domain name/description, category list + keyword scoring lists, a list of full, exact Wikipedia category names (per Stage 1's `categories.txt` format — Stage 1 uses exact matching, not substring matching, so every entry needs to be a literal category name, not a short fragment), abbreviation skip-list candidates, and 4 worked few-shot examples in the exact shape Stage 3's prompt needs (see below).
6. Write the draft to disk as separate reviewable files, not directly over the real config — e.g. `config.generated.yaml`, `categories.generated.txt`, `few_shot_examples.generated.txt` — so the user reviews before anything is overwritten. Support an explicit `--apply` flag that writes directly over the real files for users who trust the draft as-is.

**On the few-shot examples specifically:** Stage 3's prompt quality depends on 4 hand-picked worked examples that are genuinely subject-specific (see Stage 3's guide file). The LLM can draft plausible versions of these from the textbook scan, but tell the user explicitly in the generated output that these are the one part of the draft most worth a careful human read before use — a bad worked example actively teaches the Stage 3 LLM the wrong pattern for every call afterward.

**Flags:**
- `--docs <path>` — textbook source directory (required, or read from a prior partial config if one exists)
- `--dry-run` — run the full scan and print a summary, but skip the LLM call entirely and write an obviously-fake placeholder config instead, so the plumbing can be verified for free and without requiring an LLM SDK to even be installed (defer the import of whichever LLM client library is used until this flag is checked)
- `--apply` — write directly over `config.yaml`/`categories.txt` instead of `*.generated.*` files
- `--cache <path>` (default alongside the output files) — save the raw scan (file list, abbreviation frequencies, chapter grouping) to a small JSON file; Stage 4 should check for this file and reuse it instead of re-walking the same MDX files a second time, falling back to a fresh scan if it's missing or a `--no-cache` flag is passed

**Code-generation safety note:** when writing the generated config file from LLM-returned JSON, use your language's safe literal-repr formatting for every string value (Python's `repr()`/`!r`, or equivalent), not naive string interpolation — LLM-generated text will contain apostrophes and quotation marks, and naive `f"'{value}'"`-style interpolation produces a file that fails to parse. Verify this by generating a config from a profile containing values with embedded quotes and confirming the output is syntactically valid.

**No-script alternative worth documenting:** this stage automates something a user can also just do manually — paste the textbook's MDX content into a chat with an LLM and ask it to fill in the same config template. Say so explicitly in the generated README; `config-init` isn't required, it's a convenience for scanning an entire textbook at once rather than pasting excerpts by hand.

**Output:** `config.generated.yaml`, `categories.generated.txt`, `few_shot_examples.generated.txt`, and a scan cache file for Stage 4 to reuse.

---

### Stage 1 — `wiki-extract`

**Reference implementation to adapt, don't rewrite from scratch:** `reference/wiki-dumps-processing/subwiki.py` in the reference folder. It already works and is deliberately simple — no CLI flags, just three config constants at the top of the file (`DUMP_PATH`, `CATEGORIES_FILE`, `OUTPUT_FILE`). Preserve that "config lines at the top, run with no args" style if you're keeping it standalone, or wire the same three values into `config.yaml` if integrating it into the bigger CLI.

**What it does:** Streams the dump one XML page at a time using a hand-rolled line-by-line state machine (not a full XML/DOM parser — the dump is too large to load into memory). Tracks `<page>` / `<text>` boundaries manually. Only processes main-namespace articles (`<ns>0</ns>`), skips redirects.

**Category matching is exact-match, not substring, with a title fallback:**
```python
def matched_categories(page_cats, target_terms):
    return [cat for cat in page_cats if cat.strip().lower() in target_terms]

def title_matches(title, target_terms):
    return title.strip().lower() in target_terms
```
The `categories.txt` config file (renamed from `categories_clean.txt` in the reference) is a plain list of **full, exact Wikipedia category names**, one per line — not short fragments:
```
Lung cancer
Cancer research
Immunotherapy
Checkpoint inhibitors
```
A page is kept if any of its categories exactly equals one of these lines (case-insensitive), or if the page's own title exactly equals one of them.

**Substring matching was tried and rejected — build it this way, don't "improve" it back to substring matching.** It's tempting to match `cancer` as a substring so it auto-catches `"Lung cancer"`, `"Cancer research"`, etc. without enumerating each one — but substring matching also catches false positives: the term `SERS` (a real nanotech category in the reference build) matched inside the unrelated category `"Composers"`, because `"sers"` is a substring of `"composers"`. On the real reference dump, substring matching produced ~81K matches versus ~10K with exact matching, and the difference was mostly noise like that. For a curated glossary, exact matching is the correct choice even though it requires a longer, more literal category list — enumerate `"Lung cancer"` and `"Breast cancer"` as separate lines rather than relying on `cancer` to auto-expand to both.

**Wikitext cleanup applied to the lead section only** (text before the article's first `==Heading==`):
- Strip `<ref>...</ref>` reference tags
- Strip `{{templates}}`
- Resolve `[[link|display]]` → keep only the display text
- Strip bold/italic markers (`'''`, `''`) and any remaining HTML tags
- Truncate to 600 characters, breaking at the last full sentence rather than mid-word

**Per-record output fields:** `keyword` (article title), `definition_short` (cleaned lead, ≤600 chars), `source_url` (`https://en.wikipedia.org/wiki/{title}`), `images` (filenames from `[[File:...]]`/`[[Image:...]]`), `categories` (only the matched subset, not all of the page's categories).

**Output:** `subwiki.jsonl`. Print progress every 10,000 pages scanned: `Scanned 10000 pages | matched 142`.

Include a SLURM batch script template in the output for users who want to run this on an HPC cluster — the reference `subwiki.sbatch` uses `--cpus-per-task=4 --mem=16G --time=08:00:00` on a `short` partition, which is a reasonable default to suggest.

---

### Stage 2 — `wiki-clean`

**Reference implementation:** `reference/process_keywords_v2.py`. Entirely offline — no LLM calls, no API costs. Three responsibilities in one pass over Stage 1's output:

**1. Deeper markup cleanup than Stage 1's lead extraction:** decode HTML entities (`&amp;` → `&`, Greek letters like `&alpha;` → `α`), strip any remaining `{{templates}}`, `<ref>` tags, `[[File:...]]` links, image-caption artifacts (`thumb|`, `300px`, `left|`, `alt=...|`), and collapse repeated whitespace/newlines.

**2. Category assignment** — two-tier:
   - First, check if the article's own Wikipedia categories match entries in a domain-specific lookup table (config-driven — e.g. `{"immunology": "Immunology", "chemotherapy": "Pharmacology"}`)
   - If no direct match, score the cleaned text against per-category keyword lists (also config-driven), weighting a match found in the keyword itself or in the Wikipedia categories more heavily than a match buried deep in body text. Fall back to a generic default category (e.g. `"General"`) if nothing scores above zero.

**3. Relevance filtering + extractive short definition** — flag content that's obviously the wrong domain (disambiguation pages, off-topic articles that only weakly matched a category term) as `relevant: false` using a configurable list of red-flag phrases. For everything else, build `definition_short` by taking the first 2–4 sentences of the cleaned lead paragraph — try to start at the sentence that actually mentions the keyword (Wikipedia articles sometimes open with a caveat or aside before the real definition), cap around 500 characters.

**Important — this step's definitions are extractive, not generative.** It trims and selects Wikipedia's own sentences; it does not write anything new. That's intentional — it's a free, fast first pass. Stage 3 is what actually improves definition quality.

**Output:** `wiki_clean.jsonl` — same record count as Stage 1, now with `category`, `relevant`, and a rough `definition_short` added.

**Config-driven parts to expose in `config.yaml`:** the Wikipedia-category-to-app-category map, the per-category keyword scoring lists, and the relevance red-flag phrase list. These are the three things a new domain needs to customize here.

---

### Stage 3 — `wiki-rewrite`

**This is the most important stage — don't let it get shortchanged relative to the others.** It's what turns raw Wikipedia text into genuinely good, textbook-appropriate definitions.

**Reference implementation:** `reference/process_keywords_v3.py`, and its full prompt is written out in `reference/keyword_processing_guide.md` — read that file in full before implementing this stage; it contains the exact system prompt, the exact relevance rules, the exact output format requirements, and a full "Reviewer QA Standards" section describing what "correct" vs "partial" vs "incorrect" output looks like. Don't paraphrase or shorten that prompt — it's long and specific by design, because vague LLM instructions at this scale (thousands of calls) produce inconsistent results. Copy its structure and just swap the domain-specific parts (the reference is written for oncology/nanomedicine/biotechnology; a new domain needs its own relevance rules and category list, but the same shape: disambiguation check → relevance decision → category → short definition → full definition, in that order, output as one JSON object per call).

**Why this exists as a separate LLM step from Stage 2's rule-based extraction:** rule-based extraction can't fix disambiguation pages (a term like `"CTL"` might have a Wikipedia page that's just a bullet list of unrelated meanings — "Champions Tennis League... Cytotoxic T lymphocyte..." — and the extractor grabs the whole list verbatim), can't reliably catch irrelevant content that shares a category tag with real domain content (a book title, a person's biography, a physics thought experiment), and can't expand thin source material. An LLM instructed to *write* the definition from its own knowledge — using the Wikipedia text only as a factual anchor, not something to copy — fixes all three.

**Per-entry LLM call structure** (send the cleaned Stage 2 text as context):
- System prompt: sets up the LLM as a subject-matter education writer for this specific domain, defines what "relevant" and "irrelevant" mean for this textbook, lists the exact category options, and specifies the output JSON shape
- User prompt: the keyword, the cleaned Wikipedia text, and the Wikipedia categories, followed by a numbered instruction sequence — disambiguation check, relevance decision, category assignment, write `definition_short`, write `definition_raw`
- Expected response: one JSON object — `{"relevant": bool, "irrelevant_reason": str (only if false), "category": str, "definition_short": str, "definition_raw": str}`

**Built-in automated quality control after each response:** scan the returned text for leftover artifacts — wiki markup (`{{`, `[[`, `<ref`), pixel dimensions, HTML entities, plural notations like `(: amnions)`, empty parentheses, disambiguation phrasing ("can refer to:"), bullet list lines, inline citation numbers (`[1]`). If any artifact pattern is found, retry the call (cap retries — the reference uses 2). If the original Wikipedia source text was under a word-count threshold (the reference uses 50 words), flag the entry into a separate "needs human review" file regardless of whether it passed the automated check — thin source material is a weak anchor even for a capable LLM.

**Make this resumable and cost-transparent:** checkpoint progress to disk periodically (the reference checkpoints implicitly via its log + can `--resume`), log every entry's pass/fail status to a processing log file, and print an estimated cost based on the configured model and entry count before starting a full run (the reference notes roughly $155 for 10K entries on `gpt-4o`, ~$9 on `gpt-4o-mini` — make the app compute and display an equivalent estimate for whatever provider/model is configured).

**Output:** `wiki_rewrite.jsonl` + a `flagged_for_review.jsonl` for thin-source spot-checks.

**Tell the user explicitly (in the generated README) that this stage's output is a complete, usable Pipeline A result on its own.** The optional multi-model review layer described next is a nice-to-have polish pass, not a requirement.

---

### Stage 3b — Optional Multi-Model Review (build only if asked)

Do not build this by default — only implement it if explicitly requested, since it roughly triples LLM cost for a quality improvement that most projects won't need on a first pass.

If requested: take Stage 3's output and send each entry to two or three different LLM providers with a scoring prompt ("given this term, category, and definition, rate confidence 0–100 that this is accurate and well-written; tag as approved / supplementary / fix_definition / junk; give a one-line reason"). Average the scores across providers, and compute a consensus label: `AGREE` if all providers gave the same tag, `MAJORITY` if two of three did, otherwise flag for manual review. Write the results to a CSV (`review_results.csv`) with columns matching the reference format: `slug, term, category, definition, action, avg_score, {provider}_tag ×N, tag_consensus`. A final assembly step then filters Stage 3's output down to only entries with `action` in `{approved, supplementary}`, using the review CSV as an allowlist.

---

### Stage 4 — `scan`

**Reference implementation:** `reference/build_all_abbrevs.py` (the abbreviation-extraction portion of it — ignore the placeholder-stub-generation logic, since Stage 5 replaces that with real LLM enrichment).

**Input:** the textbook's own MDX/Markdown source directory, scanned recursively — unless Stage 0's scan cache exists, in which case reuse it instead of re-walking the same files (support a `--no-cache` flag to force a fresh scan if the textbook changed since Stage 0 ran).

**Detection regex:** `\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b` — matches sequences of 2+ uppercase letters, optionally followed by digits, lowercase letters, or hyphenated all-caps suffixes. Catches `VEGF`, `PD-1`, `BCR-ABL1`, `mTOR`.

**Tell the user explicitly what this regex structurally cannot catch**, so they aren't surprised by gaps: mixed-case terms that don't start with 2+ capitals (`c-ABL`, `ErbB`), and terms only ever written out in full prose in the source text (never abbreviated). If completeness matters, this needs a manual supplementary pass — not something the automated scan can be patched to fully solve.

**Per-file processing:** strip HTML/JSX tags (`<[^>]+>`) and inline code spans (`` `[^`]*` ``) before running the regex — otherwise markdown/MDX syntax pollutes the match list. Count frequency of each abbreviation across the whole corpus. Also collect up to 4 surrounding sentences per abbreviation (used as context in Stage 5).

**Noise filtering** — load a `skip` list from `config.yaml`. At minimum this should include Roman numerals and staging notations (`II`, `IIA`, `IIIB`), common markup/framework tokens (`MDX`, `JSX`, `HTML`, `CSS`, `JSON`, `API`), plain English words the regex accidentally catches (`AND`, `NOT`, `USE`, `LED`, `OFF`), and pattern-based skips for things like clinical trial registry numbers (`^NCT\d+`) or long product codes (`^[A-Z]{2,4}\d{4,}`). Also skip any abbreviation whose slug already has an entry from Stage 3's output — no need to re-define something Wikipedia already covered well.

**Output 1:** `scan_results.json` — abbreviation → `{count, context: [sentences]}`.

**Output 2:** `scan_report.csv` for human review — one row per abbreviation found, with a `status` column: `in_keywords` (already covered by Pipeline A), `skipped_noise` (matched the skip list), or `missing` (needs a Pipeline B entry). This file is the single most useful debugging tool in the whole pipeline — it's how you audit gaps after the fact.

---

### Stage 5 — `enrich`

**Reference implementation:** `reference/enrich_abbrevs.py`.

For every abbreviation flagged `missing` in Stage 4:

1. Look up a known full-name expansion from `config.abbreviation_expansions` if the user has provided one; otherwise pass the raw abbreviation to the LLM and let it infer
2. Query an external knowledge API if one is configured for the domain — the reference uses Gene Ontology (`https://api.geneontology.org/api/search/entity/autocomplete/{term}?rows=1`, no API key required) for biology-adjacent categories specifically. Support at least `gene_ontology` and `none` as config options; `mesh` (`https://id.nlm.nih.gov/mesh/lookup/descriptor`) is a reasonable second option for general medicine domains.
3. Call the LLM with the abbreviation, its full name (if known), its category, the external API result (if any), and up to 3 textbook context sentences from Stage 4. System prompt should mirror the reference's style: instruct the model to open with the full name followed by the abbreviation in parentheses, write 2–4 sentences, no bullet points, and return `{"definition_short": "...", "definition_raw": "..."}` as JSON only. Make the domain name in the prompt config-driven — swap "oncology textbook" for whatever the user configured.

Checkpoint every 25 entries to a resumable checkpoint file. Print per-entry progress: `[47/927] KRAS ✓`.

**Output:** `abbrevs_enriched.jsonl`.

---

### Stage 6 — `merge`

**Reference implementation:** `reference/merge_keywords.py`.

**Slug generation (used consistently across every stage of this app, not just here):**
```python
def make_slug(kw: str) -> str:
    s = kw.lower().replace("(", "").replace(")", "")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")
```

**Merge priority:** load all Pipeline A (Wikipedia) entries first — they always win on a slug collision, since they're grounded in a full Wikipedia article rather than just textbook context sentences. Add a Pipeline B (abbreviation) entry only if its slug isn't already present.

**Output:** `keywords_combined.jsonl`. Print a summary:
```
Wikipedia entries:        8049
Abbreviations added:       927
Abbreviations skipped:      43  (slug already covered by Wikipedia set)
Total combined:           8976
```

---

### Stage 7 — `upload`

**Reference implementation:** `reference/upload_to_supabase.py` — this file is the most production-hardened part of the whole reference codebase (retry logic, SSL error handling, content filtering). Adapt it rather than writing upload logic from scratch.

**Flags:**
- `--output <path>` — where to write `keywords.json` (default `./data/keywords.json`)
- `--no-json` — skip writing `keywords.json`, upload to Supabase only
- `--dry-run` — write `keywords.json` only, skip the Supabase upload entirely (important for local testing before a database even exists)
- `--batch-size <n>` — rows per upload batch (default 500)
- `--table <name>` — Supabase table name (default `articles`)

**Filter before anything gets written or uploaded:**
- `relevant == true`
- `len(definition_short) >= 20`
- Definition text isn't a placeholder string (basic sanity check)

**Supabase upsert request shape:**
```
POST {SUPABASE_URL}/rest/v1/{table}?on_conflict=slug
Headers:
  apikey: {SUPABASE_SERVICE_KEY}
  Authorization: Bearer {SUPABASE_SERVICE_KEY}
  Content-Type: application/json
  Prefer: resolution=merge-duplicates,return=minimal
```

**Retry logic — copy this exactly, it matters:** each batch retries up to 3 times with a short backoff (2 seconds, then 4 seconds) on any exception, not just specific error types — SSL errors in particular are common and transient when uploading thousands of rows over a normal network connection, and they should be caught and retried like any other network hiccup, not treated as fatal. If a batch still fails after 3 attempts, log it and move on to the next batch rather than aborting the whole run. Print a final count of successful vs failed rows. Because the upload is an upsert keyed on `slug`, re-running the entire script after a partial failure is always safe — already-uploaded rows are just updated in place, not duplicated.

**Credentials:** load `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (the service role key, not the anon key) from a `.env` file using a stdlib-only loader — no `python-dotenv` dependency required:
```python
def load_dotenv():
    for p in [Path(__file__).parent / ".env", Path.cwd() / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))
```

**keywords.json output format** (small, no full article text — this file ships to the browser at build time, so keep it lean):
```json
{
  "vegf": {
    "term": "VEGF",
    "definition": "Vascular endothelial growth factor (VEGF) is a signal protein...",
    "category": "Molecular Biology"
  }
}
```

---

## Domain Config File (`config.yaml`)

Everything subject-specific lives here so pointing at a new textbook only requires editing this one file, never touching the Python. The example below shows oncology values, but the structure is what matters — a law textbook's config would set `domain: "constitutional law"` and a `category_map` full of legal categories instead.

```yaml
domain: "oncology"                    # used throughout LLM prompts — change to your subject,
                                       # e.g. "constitutional law", "macroeconomics", "organic chemistry"
docs_dir: "./docs"                    # path to the textbook's MDX/MD source files
file_extensions: [".mdx", ".md"]

wikipedia_dump_path: "enwiki-latest-pages-articles.xml.bz2"
categories_file: "categories.txt"     # one exact Wikipedia category name per line, see Stage 1

# Map exact Wikipedia category names to your app's category labels.
# This example is oncology; a law textbook would use something like:
#   "contract": "Contract Law"
#   "tort": "Tort Law"
#   "criminal": "Criminal Law"
category_map:
  "oncology": "Oncology"
  "cancer": "Oncology"
  "immunology": "Immunology"
  "chemotherapy": "Pharmacology"
  "molecular biology": "Molecular Biology"

default_category: "General"

# Phrases that mean "definitely wrong domain, mark relevant=false"
relevance_red_flags:
  - "is the translation of language"
  - "may refer to:"
  - "can refer to:"

# External knowledge API for abbreviation enrichment: "gene_ontology" | "mesh" | "none"
knowledge_api: "gene_ontology"

llm_provider: "openai"                # "openai" | "anthropic"
llm_model: "gpt-4o-mini"              # cheaper default; "gpt-4o" for higher quality

# Optional: known abbreviation expansions to seed the LLM with
abbreviation_expansions:
  VEGF: "Vascular endothelial growth factor"
  KRAS: "Kirsten rat sarcoma viral proto-oncogene"

# Abbreviations to always skip as noise
skip:
  - II
  - III
  - IIA
  - IIIB
  - MDX
  - JSX
  - HTML
  - AND
  - NOT
  - USE
```

---

## Project Structure

```
keyword-pipeline/
├── cli.py                       # entry point, subcommand routing
├── config.py                    # load + validate config.yaml
├── stages/
│   ├── config_init.py           # Stage 0
│   ├── wiki_extract.py          # Stage 1
│   ├── wiki_clean.py            # Stage 2
│   ├── wiki_rewrite.py          # Stage 3 (+ optional 3b review)
│   ├── scan.py                  # Stage 4
│   ├── enrich.py                # Stage 5
│   ├── merge.py                 # Stage 6
│   └── upload.py                # Stage 7
├── utils/
│   ├── slug.py                  # make_slug()
│   ├── wiki_markup.py           # markup stripping shared by stages 1 & 2
│   ├── knowledge_apis.py        # Gene Ontology / MeSH wrappers
│   └── llm.py                   # OpenAI / Anthropic call wrapper with retry
├── config.yaml                  # domain config — the only file most users should edit
│                                 # (or let `config-init` draft it — see Stage 0)
├── categories.txt               # exact Wikipedia category names
├── .env                         # SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENAI_API_KEY
├── requirements.txt             # openai (or anthropic), pyyaml — keep this minimal
└── README.md                    # setup instructions + prerequisites, generated for the user
```

---

## Reference Files to Read Before Implementing

These are working files from the completed oncology build, kept in `reference/` in this repo purely as source material for adapting a new subject. Read the actual code, not just this prompt — the prompt summarizes the important parts, but the source is the ground truth.

| File | Read before implementing |
|------|--------------------------|
| `00_config_generator/generate_config.py` | Stage 0 — the current, working scan-and-draft implementation |
| `reference/wiki-dumps-processing/subwiki.py` | Stage 1 — the current, working, config-at-top-of-file version |
| `reference/wiki-dumps-processing/subwiki.sbatch` | Stage 1 — SLURM job template |
| `reference/wiki-dumps-processing/categories_clean.txt` | Stage 1 — example of a real 468-term category list |
| `reference/process_keywords_v2.py` | Stage 2 — full markup cleaner, category scorer, relevance filter |
| `reference/process_keywords_v3.py` | Stage 3 — LLM call structure, retry logic, quality checker |
| `reference/keyword_processing_guide.md` | Stage 3 — **the actual full prompt text; read this in full, don't paraphrase it** |
| `reference/build_all_abbrevs.py` | Stage 4 — regex, skip set, MDX scanning approach |
| `reference/enrich_abbrevs.py` | Stage 5 — Gene Ontology lookup, LLM call, checkpointing |
| `reference/merge_keywords.py` | Stage 6 — slug dedup, priority logic |
| `reference/upload_to_supabase.py` | Stage 7 — production-hardened upload, retry/SSL handling |
| `reference/KeywordPopup.tsx` | UI reference — the Next.js component this pipeline's output feeds into |

**Do not reference or use `reference/wiki-dumps-processing/subwiki_nik.py`** — it's an earlier draft that has since been superseded by `subwiki.py` and is no longer part of the working pipeline.

The two files most worth copying closely rather than reinventing are `reference/wiki-dumps-processing/subwiki.py` (Stage 1) and `reference/upload_to_supabase.py` (Stage 7) — both are production-hardened in ways that are easy to get subtly wrong from scratch (memory-safe XML streaming, and network-failure-tolerant batch uploads, respectively).
