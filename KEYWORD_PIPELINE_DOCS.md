# Keyword Popup Pipeline — Full Documentation

## What This Builds

A keyword popup system for any textbook, on any subject. Every technical or subject-specific term in the textbook gets an inline tooltip with a short definition, a full article view, and a source link. There are two independent pipelines that merge at the end:

- **Pipeline A** — Wikipedia XML dump → curated keyword definitions (the bulk of the content — thousands of terms, covering whatever your subject's Wikipedia footprint looks like)
- **Pipeline B** — Textbook MDX scan → abbreviation definitions (catches subject-specific abbreviations and shorthand that Wikipedia articles don't cover well — `VEGF` and `PD-1` for a medical textbook, `GDP` and `NPV` for an economics textbook, `SCOTUS` and `TRO` for a law textbook)

**Everything is configured by two things: a Wikipedia category list and an abbreviation skip list.** Change those two inputs and the same scripts work for a different subject — stream a Wikipedia dump, filter by category, clean the text, have an LLM write proper definitions, scan the textbook for shorthand terms, merge, upload.

**Starting a new subject from zero? Don't write the category list and config by hand — paste your textbook in first.** Your textbook's own MDX/Markdown source files (chapter folders, numbered `.mdx` files, `meta.json` per chapter — whatever your docs platform's layout is) already contain most of the signal needed to draft a config: the subject, the topic breakdown, and the abbreviations actually used. `00_config_generator/generate_config.py` scans that textbook and makes one LLM call to draft the whole config for you — Wikipedia category list included — instead of you consulting Wikipedia and an LLM chat window by hand. See "Step 0" below; it's the recommended first thing to run.

If you are starting this from zero, read this document top to bottom once before running anything. It explains what each script does and why, in the order you'd actually run them.

---

## What You Need Before Starting

| Requirement | Why | Where to get it |
|---|---|---|
| Python 3.10+ | All scripts are Python | python.org |
| A Wikipedia XML dump | Source data for Pipeline A | `https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles.xml.bz2` (~20GB compressed) |
| OpenAI API key | Definitions are LLM-written, not copied from Wikipedia verbatim | platform.openai.com |
| A Supabase project | Stores the final keyword database | supabase.com — free tier is enough to start |
| Your textbook's MDX/Markdown files | Needed twice: to draft your config (Step 0) and for Pipeline B (abbreviation scanning) | wherever your textbook content lives |
| (Optional) Access to a compute cluster with SLURM | The Wikipedia dump is large; a laptop can run it but slowly (several hours) | your university/org's HPC, or just run locally overnight |

You do **not** need a Gene Ontology account or any other external API key — Gene Ontology's public API used in Pipeline B is unauthenticated.

---

## Complete Chain (accurate, real script names)

This is subject-agnostic. Whatever your textbook is about, the shape of the chain is identical — only the category list, the category labels, and the relevance rules change per subject.

```
Step 0 — Config Draft (run this first for a new subject)
───────────────────────────────────────────────────────
Your textbook's MDX/MD files + meta.json
    └─► generate_config.py             scan textbook, one LLM call drafts config
            └─► config.generated.py, categories.generated.txt,
                few_shot_examples.generated.txt, textbook_scan_cache.json
                    (review, then apply — becomes config.py / categories.txt below)

Pipeline A — Wikipedia
───────────────────────
enwiki XML dump
    └─► subwiki.py                          stream dump, filter by category (exact match + title fallback)
            └─► subwiki.jsonl                raw JSONL, one record per matched article
                    └─► process_keywords_v2.py     regex-clean markup, assign category,
                    │                                filter obvious junk, extract a short
                    │                                definition (rule-based, no LLM yet)
                    │       └─► keywords_clean.jsonl
                    │
                    └─► process_keywords_v3.py     LLM (OpenAI) rewrites every definition
                                                      from scratch, resolves disambiguation,
                                                      does a strict relevance check
                            └─► keywords_v3.jsonl   ← this is your usable Pipeline A output
                                                       for a new textbook, on any subject

                            (optional extra QA pass — see "Quality Review" below)
                            └─► multi-model review → final_keywords.csv
                                    └─► process_keywords_v2_5.py  (fills any remaining gaps)
                                            └─► keywords_v2.5.jsonl
                                                    └─► filter_to_v5.py
                                                            └─► keywords_v5.jsonl

Pipeline B — Abbreviations
───────────────────────────
Your MDX textbook files (any subject)
    └─► build_all_abbrevs.py    scan + regex extract abbreviations/shorthand
            └─► keywords_abbrevs.jsonl
                    └─► enrich_abbrevs.py    LLM (+ optional external reference API)
                    │                          writes real definitions
                            └─► keywords_abbrevs_enriched.jsonl
                                    └─► merge_keywords.py   (Pipeline A wins on collision)
                                            └─► keywords_combined.jsonl
                                                    └─► upload_to_supabase.py
                                                            └─► Supabase + keywords.json
```

**The short version for starting a new textbook from scratch:** run Pipeline A through `reference/process_keywords_v3.py` and stop there — that output (`keywords_v3.jsonl`) is already clean, relevance-filtered, and has real LLM-written definitions. The multi-model review step (`final_keywords.csv` etc.) that produces `keywords_v5.jsonl` is an extra quality-assurance pass on top of v3 — a nice-to-have, not a requirement. Treat `keywords_v3.jsonl` as your "v5-equivalent" and feed it directly into `reference/merge_keywords.py` in Pipeline B.

---

## Step 0 — Draft Your Config From the Textbook Itself

**Reference implementation:** `00_config_generator/generate_config.py`.

**Why this exists:** every step below depends on `config.py`/`categories.txt` already being filled in for your subject — the category list, the category labels, the relevance rules, the domain name used in every LLM prompt. Writing that from scratch means browsing Wikipedia's category tree and guessing at abbreviations your textbook might use. But your textbook's own MDX source already has this information in it, and you need to point the pipeline at that directory anyway (Pipeline B scans it later) — so scan it first and let an LLM draft the config from real content instead of guesswork.

**Input:** your textbook's MDX/MD source directory, recursively — chapter folders full of numbered `.mdx` files, usually with a `meta.json` per chapter. For example:
```
docs/
├── Chapter1/
│   ├── meta.json
│   ├── 1_Introduction_to_Cells.mdx
│   └── 6_Tissue_Organization_And_Organ_Systems.mdx
└── AppendixB/
    └── ...
```
The script doesn't require this exact layout — it walks recursively and reads whatever `meta.json` and `.mdx`/`.md` files it finds — but this is the shape it's built around.

**What it does:**
1. Walks every `.mdx`/`.md` file, groups them by parent folder (chapter), strips JSX/HTML/code fences, and extracts a title per file (first `# Heading`, or derived from the filename).
2. Reads every `meta.json` it finds — these usually have a cleaner chapter title than filename-guessing would produce.
3. Runs the same abbreviation-frequency scan Pipeline B's `reference/build_all_abbrevs.py` uses, so the config draft is grounded in abbreviations your textbook actually contains, not invented from the subject name alone.
4. Sends a compact profile of all of the above to an LLM, which returns a complete draft: domain name/description, category list + keyword scoring lists, a list of full, exact Wikipedia category names (this becomes `categories.txt` — Step A1 uses exact matching, not substring matching, so this list needs to be literal category names, not short fragments), abbreviation skip-list candidates, and 4 worked few-shot examples in the shape `reference/process_keywords_v3.py`'s prompt needs.
5. Writes the draft to separate `*.generated.*` files (not directly over your real config) so you can review before anything is overwritten, and caches the raw scan to `textbook_scan_cache.json` so Pipeline B's abbreviation scan can reuse it instead of re-walking the same MDX files.

**Run it:**
```bash
cd 00_config_generator
python generate_config.py --docs ../docs --dry-run   # free smoke test, no LLM call
python generate_config.py --docs ../docs             # real run, one LLM call
python generate_config.py --docs ../docs --apply     # write straight over config.py / categories.txt
```

**Review before trusting it — especially the few-shot examples.** The LLM draft is a strong starting point, not a final answer. The 4 worked examples it drafts for Step A3's prompt are the single part most worth a careful read: a bad worked example teaches the LLM the wrong pattern for every call afterward, not just one.

**No script, no problem:** you can skip running this file entirely and just paste your textbook's MDX content into a chat with an LLM instead, asking it to fill in the same config template — the script just automates that same conversation across an entire textbook instead of a pasted excerpt. See the module docstring at the top of `generate_config.py` for the prompt to use if you go this route.

**Output:** `config.generated.py`, `categories.generated.txt`, `few_shot_examples.generated.txt`, `textbook_scan_cache.json`.

---

## Pipeline A — Wikipedia XML Dump

### Step A1 — `subwiki.py` (XML stream → raw JSONL)

**What it does:** Streams the Wikipedia XML dump one page at a time (never loads the whole 90GB+ file into memory) and keeps only pages whose Wikipedia categories match your domain.

**Configuration is three lines at the top of `subwiki.py` — no naming convention to follow, just set these three values yourself:**
```python
DUMP_PATH       = "enwiki-latest-pages-articles.xml.bz2"   # path to the dump file you downloaded
CATEGORIES_FILE = "categories_clean.txt"                    # your category list — see below
OUTPUT_FILE     = "subwiki.jsonl"                            # output filename — call it anything
```
`OUTPUT_FILE` is just a plain string you set; there's no auto-naming logic, no template, nothing derived from the category list. Edit the value, save the file, run it.

Then run:
```bash
python subwiki.py
```
No command-line flags — everything is configured in the file itself. For a cluster run, the SLURM batch script (`subwiki.sbatch`) just calls `python subwiki.py` directly (see that file for the resource settings used: 4 CPUs, 16GB RAM, 8-hour time limit).

**Why streaming matters:** A full Wikipedia dump has ~25 million pages. Loading it into memory isn't possible on a normal machine. The script reads the XML line by line with a hand-rolled state machine (tracking when it's inside a `<page>` tag, a `<text>` tag, etc.) so memory use stays constant regardless of dump size. It also skips non-article pages (talk pages, user pages — anything where `<ns>` isn't `0`) and skips redirects.

**How category matching works — exact match, plus a title fallback:**
```python
def matched_categories(page_cats, target_terms):
    return [cat for cat in page_cats if cat.strip().lower() in target_terms]

def title_matches(title, target_terms):
    return title.strip().lower() in target_terms
```
`categories_clean.txt` is a plain text file, one **full, exact Wikipedia category name** per line — not short fragments:
```
Lung cancer
Cancer research
Breast cancer
Immunotherapy
Checkpoint inhibitors
```
A page is kept if any of its `[[Category:...]]` tags *exactly equals* one of these lines (case-insensitive), or if the page's own title exactly equals one of them (the title check catches concept pages whose categories don't line up cleanly with the topic — e.g. a page titled exactly `"Cancer"` with only broad, generic categories).

**Substring matching was tried first and rejected — this is a real, measured result, not a style preference.** A term like `cancer` matching *inside* any category name sounds convenient (it catches `"Lung cancer"`, `"Cancer research"`, `"Breast cancer awareness"` automatically, no need to enumerate each one) — but it also matches inside anything else that happens to contain that substring. The concrete example that killed this approach: the term `SERS` (a real nanotech category) matched inside the unrelated Wikipedia category `"Composers"`, because `"sers"` is literally a substring of `"composers"`. On the real dump, substring matching produced roughly 81,000 matches versus roughly 10,000 with exact matching — and the ~71,000-match difference was mostly noise like that, not real recall gained. For a curated textbook glossary, that tradeoff isn't worth it: exact matching is the correct default.

**The real cost of exact matching, and why it's still worth it:** because it requires an exact string match, `categories_clean.txt` has to explicitly enumerate every category variant you want — `cancer` alone won't catch `"Category:Lung cancer"` anymore, you need `"Lung cancer"` as its own line. This is more work up front than a short fragment list, but it's the only way to get clean matches. Budget for a longer, more literal list than you might expect — enumerate specific categories rather than relying on a short list of root words to auto-expand via substring matching.

**Building your own `categories_clean.txt` for any subject:** Browse Wikipedia's category tree for your subject and copy down the *exact* category names (as they appear after `Category:` on Wikipedia, capitalization included where it matters — matching is case-insensitive, but copy the real name rather than guessing) that should trigger a match — for cardiology, start at `https://en.wikipedia.org/wiki/Category:Cardiology`; for constitutional law, `https://en.wikipedia.org/wiki/Category:Constitutional_law`; for macroeconomics, `https://en.wikipedia.org/wiki/Category:Macroeconomics` — and walk the subcategory tree, copying each relevant category's exact name as its own line rather than trying to shorten it to a root word. Expect a larger list than a fragment-style approach would need — a few hundred to a couple thousand exact names is normal for a single-subject textbook, since each variant (`"Lung cancer"`, `"Breast cancer"`, `"Cancer research"`, ...) needs its own line.

**Per-page output fields:**
- `keyword` — the article title, e.g. `"VEGF"`
- `definition_short` — the lead section (everything before the article's first `==Heading==`), with wiki markup stripped and truncated to 600 characters at a sentence boundary
- `source_url` — `https://en.wikipedia.org/wiki/{title}`
- `images` — filenames pulled from any `[[File:...]]` or `[[Image:...]]` tags in the lead
- `categories` — only the categories that matched your terms list (not all of the page's categories)

**Output:** your raw JSONL file — record count depends entirely on your category list and how large your subject's Wikipedia footprint is. Example line:
```json
{
  "keyword": "VEGF",
  "definition_short": "Vascular endothelial growth factor (VEGF) is a signal protein...",
  "source_url": "https://en.wikipedia.org/wiki/VEGF",
  "images": ["VEGF_Receptor_Activation.png"],
  "categories": ["Cancer research", "Angiogenesis"]
}
```

At this point the text is still messy — Wikipedia lead sections often still contain leftover markup, disambiguation notices, or come from clearly irrelevant articles that only weakly matched a category term. The next two steps clean this up.

---

### Step A2 — `reference/process_keywords_v2.py` (rule-based cleaning, no LLM cost)

**What it does:** Runs entirely offline with no API calls. Three jobs in one pass:

**1. Strips remaining wiki markup** more thoroughly than Step A1's lead extraction did: removes `{{templates}}`, decodes HTML entities (`&amp;` → `&`, Greek letters, etc.), strips `<ref>` citation tags, removes `[[File:...]]` links, converts `[[link|display]]` → `display`, strips leftover image-caption artifacts (`thumb|`, `300px`, `left|`), and collapses excess whitespace.

**2. Assigns a category.** First tries to match the page's own Wikipedia categories against a lookup table (`WIKI_CAT_MAP`) that maps Wikipedia category words to your app's category labels — in the oncology reference build, `"immunology"` → `Immunology`, `"chemotherapy"` → `Pharmacology`; for a law textbook this would instead be something like `"contract"` → `Contract Law`, `"tort"` → `Tort Law`. If no direct match, it scores the cleaned text against per-category keyword lists (`CATEGORY_KEYWORDS`) and picks the highest-scoring category, weighting matches found in the keyword itself or in the Wikipedia categories more heavily than matches buried in the body text. Falls back to a generic catch-all category if nothing scores above zero (the reference build calls this `Biomedical Science`; rename it to whatever fits your subject, e.g. `General Legal Concept`).

**3. Filters obvious irrelevant content and builds `definition_short`.** A content-signal check (`NON_BIO_CONTENT_SIGNALS` in the reference — rename per subject) flags things like language-translation articles, disambiguation pages, and other clearly-wrong-domain content and marks them `relevant: false`. For everything else, `make_definition_short()` pulls the first 2–4 sentences of the cleaned lead paragraph (trying to start the definition at the sentence that actually contains the keyword, in case the article opens with a caveat or aside) and caps it around 500 characters.

**This step generates real definition text, but it's extractive, not generative** — it's just trimming and selecting from Wikipedia's own wording, not writing anything new. That's why Step A3 exists, regardless of subject.

**Output:** `keywords_clean.jsonl` — same record count as Step A1's output, now with clean text, a `category`, and a rough `definition_short`. Some entries will still have `relevant: false` (irrelevant Wikipedia matches caught by the content filter) or weak, still-Wikipedia-voiced definitions.

**To point this at a new subject:** Edit `WIKI_CAT_MAP` and `CATEGORY_KEYWORDS` at the top of the file to swap in your field's categories and keyword lists. For a law textbook that's `Contract Law`, `Tort Law`, `Criminal Law` instead of `Oncology`, `Immunology`, `Pharmacology`. For a computer science textbook it's `Algorithms`, `Data Structures`, `Networking` instead. Update the content-signal list if your subject has its own common false-positive patterns (e.g. a history textbook might need to explicitly exclude sports/entertainment articles that share a date-related category).

---

### Step A3 — `reference/process_keywords_v3.py` (LLM rewrites every definition)

This is the step that actually makes the definitions good. **Run this step — it's the one that matters most.**

**Why it exists:** Step A2's extractive definitions have real problems that pattern-matching alone can't fix:
- **Disambiguation pages** — a term like `CTL` has a Wikipedia page that just lists unrelated meanings ("Champions Tennis League... Cytotoxic T lymphocyte..."); the naive extractor grabbed the whole list.
- **Irrelevant terms slipping through** — books, biographies, and thought experiments that happen to share a category tag with real domain content (e.g. "The Selfish Gene" is a book, not a biology concept).
- **Thin or malformed definitions** — some Wikipedia leads are one sentence long, or retain stray artifacts like `(: amnia)` plural notations.

**What it does:** For each entry in `keywords_clean.jsonl`, sends the cleaned Wikipedia text to an LLM (OpenAI's API — `gpt-4o` by default, `gpt-4o-mini` for a cheaper run) with detailed instructions to:
1. Detect and resolve disambiguation pages to the correct domain meaning
2. Make a strict relevant/not-relevant call (the full rules are written out in the system prompt inside the script)
3. Assign one specific category
4. Write a fresh `definition_short` (2–4 sentences, written *from the model's own knowledge*, using the Wikipedia text only as a factual anchor — not copied or paraphrased) and a fresh `definition_raw` (a longer, 3-paragraph article covering what the term is, how it works, and why it matters)

The full system and user prompt text — including every relevance rule and every formatting requirement — is written out in `reference/keyword_processing_guide.md` in this folder. Read that file if you're adapting this step to a new domain; the prompt is long and detailed by design, because vague instructions produce inconsistent LLM output at this scale.

> **Naming note:** the guide document calls this the "Claude-Rewrite Pipeline" because the *concept* is "the model writes definitions fresh instead of copying Wikipedia" — but the actual script implementation calls the OpenAI API, not Claude. Swap in whichever LLM you prefer; the prompt itself is model-agnostic.

**Built-in quality control:** After each LLM response, the script runs an automated artifact checker (`check_quality()`) that scans for leftover markup, disambiguation phrases, empty parentheses, citation numbers, and other tell-tale signs the LLM didn't fully clean the text. Entries that fail get retried automatically (up to `MAX_RETRIES`, default 2). Entries whose original Wikipedia source was very short (`THIN_THRESHOLD`, default 50 words) get flagged into a separate `flagged_for_review.jsonl` file for a human to spot-check later — a thin source is a weak anchor for the LLM, so these are worth a second look even after passing the automated checker.

**Run it:**
```bash
export OPENAI_API_KEY=sk-...
python process_keywords_v3.py                      # full run, gpt-4o (best quality, ~$155 for 10K terms)
python process_keywords_v3.py --model gpt-4o-mini  # cheaper (~$9 for 10K terms)
python process_keywords_v3.py --resume              # continue an interrupted run
```

It logs progress to `v3_processing.log` and writes checkpoints as it goes, so `--resume` picks up where a crashed or rate-limited run left off.

**Output:** `keywords_v3.jsonl` (plus `flagged_for_review.jsonl` for thin-source spot-checks).

**This is your finish line for Pipeline A.** Everything after this point (multi-model review, `keywords_v2.5.jsonl`, `reference/filter_to_v5.py`) is an additional quality-assurance layer — documented below for completeness, but optional.

---

### Optional — Multi-Model Quality Review (advanced, not required to start)

For the oncology textbook, `keywords_v3.jsonl` went through one more quality gate before shipping: every entry was independently scored and tagged by **three different LLMs** (Mistral, OpenAI, and Claude), each asked "is this entry accurate, relevant, and well-defined?" Their tags (`approved` / `supplementary` / `fix_definition` / `junk`) and an average confidence score were combined into a consensus label (`AGREE` when all three concurred, `MAJORITY` when two of three did). This review process doesn't live in this repo — it's a separate, working project: **[keyword-qa-pipeline](https://github.com/JanviChitroda24/keyword-qa-pipeline)**, which packages this exact review layer as its own numbered stage sequence (`10_`–`25_`, continuing on from this repo's `00_`–`07_`) and also covers scoring the abbreviation set from Pipeline B, not just Pipeline A. Its README reports its own final oncology counts (production run: 8,161 scored keyword rows, 8,053 approved for the textbook; 927 scored acronyms, 915 approved) — numbers from a later, more polished pass than the ones quoted below, so treat the two as related but not identical runs. Its *output* is:

- **`final_keywords.csv`** (8,054 rows) — the primary approved term list. Every row has `slug, term, category, definition, action, avg_score, mistral_tag, openai_tag, claude_tag, tag_consensus`.
- **`approved_supplementary.csv`** (6,722 rows) — a secondary, lower-confidence approved list (used to build an alternate `keywords_v4.jsonl` that ended up not being used downstream — see note below).
- **`fix_definition.csv`** (339 rows) — terms where the definition was missing or all three models flagged something wrong; these need to be regenerated, not just approved.

If you want to replicate this for your own subject, the pattern is: take your `keywords_v3.jsonl`, send each entry's `keyword` + `definition_short` + `definition_raw` to two or three different LLM providers with a scoring prompt ("rate 0–100, tag as approved/supplementary/fix_definition/junk, explain why"), average the scores, and write out a CSV with the results. Nothing here is medical or oncology-specific — the same scoring prompt structure works for a law textbook or a chemistry textbook, you're just scoring different content. This is genuinely optional — a single well-prompted `reference/process_keywords_v3.py` pass is good enough for most first builds, whatever the subject. Only add multi-model review if you need an extra layer of confidence (e.g. the textbook will be published and errors are costly).

**`reference/process_keywords_v2_5.py`** is a separate, cheaper pass that runs on `keywords_clean.jsonl` (the Step A2 output, not v3) and does the minimum necessary: pass through any entry that already has a `definition_short`, and only call the LLM for entries where it's missing or too short (< 20 characters). It marks every entry `relevant: true` — the assumption being that filtering already happened via `final_keywords.csv`. This is really a bulk gap-filler for the approved-terms list, not a general-purpose cleaning step. Output: `keywords_v2.5.jsonl`.

**`reference/filter_to_v5.py`** is the final assembly step: it takes the slug list from `final_keywords.csv`, looks up each slug's definition in `keywords_v2.5.jsonl`, and writes matched entries — tagged with the review metadata (`_review_action`, `_review_avg_score`, `_review_consensus`) — to `keywords_v5.jsonl`. This is the file the oncology project's `reference/merge_keywords.py` actually uses downstream.

There is also a **`reference/filter_to_v4.py`** which builds `keywords_v4.jsonl` from `approved_supplementary.csv` + `fix_definition.csv` with definitions pulled from `keywords_v3.jsonl` directly. This was an alternate/parallel output — it exists in the codebase but was **not** the file used in the final merge. If you're starting fresh, ignore v4 entirely; it's an artifact of iterating on the review process, not a required pipeline stage.

---

### Next.js Integration

`keywords.json` (produced by `reference/upload_to_supabase.py` in Pipeline B, described below) is copied into the `data/` folder of the Next.js project. At build time, a remark plugin (`remark-keyword-popup.mjs`) builds a word-level reverse index from this file — including basic plural matching — and wraps every matching word found in the MDX content with a `<KeywordPopup slug="...">` component automatically. You don't manually tag words in the textbook; the build step finds them.

---

## Pipeline B — Abbreviation Scanning

Wikipedia articles cover full-word terms well ("Vascular endothelial growth factor") but often don't have a dedicated article for the *abbreviation itself* used the way your textbook uses it ("VEGF"). The same gap exists in any subject — a law textbook will use "TRO" constantly without a Wikipedia article titled just "TRO," an economics textbook will lean on "GDP" and "NPV" the same way. Pipeline B scans your actual textbook content for these abbreviations and writes definitions for the ones Pipeline A missed, regardless of subject.

### Step B1 — `reference/build_all_abbrevs.py` (MDX scan → abbreviation candidates)

**Input:** All `.mdx` (or `.md`) files under your textbook's content directory — or, if you ran Step 0, its `textbook_scan_cache.json` is reused automatically instead of re-walking the same files a second time.

**Detection regex:** `\b([A-Z]{2,}[A-Za-z0-9]*(?:-[A-Z0-9]+)*)\b`

This catches sequences of 2+ uppercase letters, optionally followed by digits/lowercase/hyphenated suffixes: `VEGF`, `PD-1`, `BCR-ABL1`, `mTOR`.

**What it structurally cannot catch** (know this going in, so you're not surprised by gaps later):
- Mixed-case terms that start with a single lowercase-then-uppercase pattern: `c-ABL`, `ErbB` — the regex requires the match to *start* with 2+ capitals
- Slash-separated compound terms: `PI3K/AKT` gets matched as one token if adjacent, but terms separated by other punctuation may be missed
- Terms only ever written in full prose, never abbreviated in your source text

If completeness matters for your domain, budget time for a manual pass afterward — grep your MDX files for the specific abbreviation patterns your subject uses and cross-check against what the automated scan found.

**Noise filtering (the `SKIP` set):** A hard-coded list removes common false positives before they get treated as real abbreviations — Roman numerals and staging notations (`II`, `IIA`, `IIIB`), cell line names (`MCF7`, `HCT116`), lab reagent codes (`NP-40`, `PVDF`), clinical trial registry numbers (matched via `NCT\d+`), framework/markup tokens (`MDX`, `JSX`, `HTML`), and plain English words the regex accidentally matches (`AND`, `NOT`, `USE`, `LED`).

**Definition sourcing:** In the oncology reference build, ~484 abbreviations were hand-curated as compact tuples `(keyword, full_name, category, wiki_title)` across four files (`abbrev_defs.py` through `abbrev_defs4.py`) — this hand-curation step is optional and subject-specific; you can skip it entirely for a new textbook and let every abbreviation go straight to Step B2 for LLM enrichment. Anything the scan finds that isn't in a hand-curated list (or if you skip hand-curation entirely, everything) gets a placeholder definition, which Step B2 replaces with something real.

**Output:** `keywords_abbrevs.jsonl`

---

### Step B2 — `reference/enrich_abbrevs.py` (LLM writes real definitions)

For every abbreviation entry, three sources are combined and sent to an LLM:

1. **Textbook context** — up to 4 real sentences pulled from your MDX files showing how the abbreviation is actually used in context (built by the same MDX scan, just collecting surrounding sentences instead of just counting)
2. **External knowledge API (optional, swap per subject)** — the reference build looks up Gene Ontology (`https://api.geneontology.org/api/search/entity/autocomplete/{term}`) for Molecular Biology / Genomics / Immunology terms specifically, to pull an authoritative label and definition if one exists. For a different subject, swap in whatever reference API fits — a legal-terms API for a law textbook, a public economics glossary for an econ textbook — or skip this source entirely and rely on textbook context alone; it's not a required input.
3. **LLM call** — `gpt-4o-mini` by default, given the abbreviation, its full name (if known), its category, the external API result (if any), and the textbook context, with a system prompt like this one:

```
You are a biomedical lexicographer writing definitions for an oncology textbook.
Write a clear, factual definition in the style of a medical encyclopedia.
- Start with the full name followed by the abbreviation in parentheses.
- 2–4 sentences. No bullet points.
- definition_short: first 1–2 sentences, max 280 characters.
- definition_raw: complete 2–4 sentence definition.
- Return JSON only: {"definition_short": "...", "definition_raw": "..."}
```

Progress checkpoints every 25 entries so the run is fully resumable if interrupted.

**Output:** `keywords_abbrevs_enriched.jsonl` — the entry count depends entirely on how many abbreviations your own textbook uses.

**To point this at a new subject:** swap the Gene Ontology call for whatever external knowledge base fits your field (MeSH for general medicine, an ICD-11 lookup for clinical terms, or skip external lookup entirely and rely on textbook context alone — this is genuinely optional), and change "oncology textbook" in the system prompt to your subject, e.g. "You are a legal lexicographer writing definitions for a constitutional law textbook."

---

### Step B3 — `reference/merge_keywords.py`

Combines the Pipeline A output (`keywords_v5.jsonl`, or `keywords_v3.jsonl` if you skipped the optional review layer) with the Pipeline B output (`keywords_abbrevs_enriched.jsonl`).

**Merge logic — Pipeline A always wins:**
```python
def make_slug(kw: str) -> str:
    s = kw.lower().replace("(", "").replace(")", "")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")

# Load Pipeline A first — these entries take priority
# Add Pipeline B entries only if their slug isn't already present
```
Every keyword gets slugified (lowercased, parentheses removed, non-word characters stripped, spaces/underscores turned into hyphens). If an abbreviation's slug already exists from Pipeline A (e.g. Wikipedia already had a good `VEGF` article), the Pipeline A version wins — it's generally richer since it's grounded in a full Wikipedia article rather than just textbook context. This logic is identical for any subject; there's nothing to change here.

**Output:** `keywords_combined.jsonl` — Pipeline A's count plus whatever new abbreviations Pipeline B found that Pipeline A didn't already cover.

---

### Step B4 — `reference/upload_to_supabase.py`

The final step. Filters the combined file, then writes output and/or uploads to your database.

**Filter criteria before anything gets uploaded:**
- `relevant` must be `true`
- `definition_short` must be at least 20 characters
- Definition text must pass a basic content sanity check (not a placeholder string)

**Run it:**
```bash
# Set credentials first (in a .env file, or as environment variables):
#   SUPABASE_URL=https://your-project.supabase.co
#   SUPABASE_SERVICE_KEY=your-service-role-key   (NOT the anon key)

python upload_to_supabase.py --input keywords_combined.jsonl --output ./data/keywords.json
```

This writes `keywords.json` (for the Next.js popup component) **and** upserts every entry to your Supabase `articles` table. Flags:
- `--no-json` — skip writing `keywords.json`, upload to Supabase only
- `--dry-run` — write `keywords.json` only, skip the Supabase upload (useful for testing locally before you have a database set up)
- `--batch-size 500` — how many rows per upload batch (default 500)

**Reliability:** Each batch retries automatically up to 3 times with a short backoff (2s, then 4s) if the upload fails — this covers transient network issues and SSL errors, which are common when uploading thousands of rows. Because the upload uses upsert (matching on `slug`), it is always safe to just re-run the whole script if something fails partway through; already-uploaded rows are simply updated in place, not duplicated.

---

## JSONL Entry Schema

Every entry, whether it came from Pipeline A or Pipeline B, ends up in this shape by the time it reaches `keywords_combined.jsonl`:

```json
{
  "keyword": "VEGF",
  "source_url": "https://en.wikipedia.org/wiki/Vascular_endothelial_growth_factor",
  "images": ["https://upload.wikimedia.org/..."],
  "categories": ["Vascular endothelial growth factor"],
  "all_page_categories": ["Vascular endothelial growth factor", "Molecular Biology"],
  "relevant": true,
  "category": "Molecular Biology",
  "definition_short": "Vascular endothelial growth factor (VEGF) is a signal protein...",
  "definition_raw": "Vascular endothelial growth factor (VEGF) is a signal protein produced by many cells...",
  "_review_action": "approved",
  "_review_avg_score": "92",
  "_review_consensus": "AGREE",
  "_abbrev_entry": false
}
```

`_abbrev_entry: true` marks entries that came from Pipeline B (the abbreviation scanner) rather than Wikipedia. The `_review_*` fields are only populated if you ran the optional multi-model review layer — otherwise they'll be empty strings.

---

## Supabase `articles` Table Schema

| Column | Type | Notes |
|--------|------|-------|
| `slug` | text (PK) | unique identifier, e.g. `vegf` — used for upsert matching |
| `keyword` | text | display name |
| `definition_short` | text | tooltip text |
| `definition_raw` | text | full article body |
| `category` | text | domain category |
| `source_url` | text | Wikipedia URL, or empty for abbreviation-only entries |
| `images` | jsonb | array of image URLs |
| `relevant` | boolean | `false` rows are excluded from the popup at query time |

Create this table before your first `reference/upload_to_supabase.py` run. A minimal SQL definition:
```sql
create table articles (
  slug text primary key,
  keyword text,
  definition_short text,
  definition_raw text,
  category text,
  source_url text,
  images jsonb,
  relevant boolean default true
);
```

---

## Adapting the Whole Pipeline to a New Domain — Checklist

| Step | File to edit | What to change |
|---|---|---|
| 0 | `generate_config.py` | `--docs` pointed at your textbook — drafts everything below for you |
| A1 | `subwiki.py` | 3 config lines: dump path, categories file, output filename |
| A1 | `categories_clean.txt` | Replace with your domain's exact Wikipedia category names |
| A2 | `reference/process_keywords_v2.py` | `WIKI_CAT_MAP`, `CATEGORY_KEYWORDS`, `NON_BIO_CONTENT_SIGNALS` |
| A3 | `reference/process_keywords_v3.py` / `reference/keyword_processing_guide.md` | System prompt: swap the domain name and relevance rules |
| B1 | `reference/build_all_abbrevs.py` | `SKIP` set, `infer_category()` patterns, `DOCS_ROOT` path |
| B2 | `reference/enrich_abbrevs.py` | External API (`go_lookup()`), system prompt domain name, `DOCS_ROOT` path |

Everything else — slug generation, the merge priority logic, the Supabase upload script, the Next.js popup component — is domain-agnostic and works unchanged.

---

## File Reference

| File | Pipeline | Purpose |
|------|----------|---------|
| `00_config_generator/generate_config.py` | Step 0 | Scan textbook, LLM drafts `config.py` + `categories.txt` |
| `reference/wiki-dumps-processing/subwiki.py` | A, Step 1 | Stream Wikipedia XML dump → raw JSONL |
| `reference/wiki-dumps-processing/subwiki.sbatch` | A, Step 1 | SLURM job template for cluster runs |
| `reference/wiki-dumps-processing/categories_clean.txt` | A, Step 1 | 468 oncology category terms (currently fragment-style — see Step A1 for why exact category names are preferred) |
| `reference/process_keywords_v2.py` | A, Step 2 | Rule-based markup cleaning, category assignment, relevance filter |
| `reference/process_keywords_v3.py` | A, Step 3 | LLM rewrites every definition — **this is the key quality step** |
| `reference/keyword_processing_guide.md` | A, Step 3 | Full system/user prompt text and QA rules used by v3 |
| `reference/process_keywords_v2_5.py` | A, optional | Cheap gap-filler for entries missing a definition |
| `reference/filter_to_v5.py` | A, optional | Final assembly using multi-model review results |
| `reference/filter_to_v4.py` | A, unused | Alternate assembly path — not used in final output, safe to ignore |
| `reference/sample_for_review.py` | A, QA | Pulls a stratified random sample from Supabase for manual spot-checking after upload |
| `reference/build_all_abbrevs.py` | B, Step 1 | Scan MDX → extract abbreviations |
| `reference/enrich_abbrevs.py` | B, Step 2 | LLM-enrich abbreviations with context + Gene Ontology |
| `reference/merge_keywords.py` | B, Step 3 | Merge Pipeline A + B outputs |
| `reference/upload_to_supabase.py` | B, Step 4 | Filter, write `keywords.json`, upload to Supabase |
| `reference/KeywordPopup.tsx` | UI | Next.js hover tooltip + full-article drawer component |

The rows below are **not included in this repo** — they're mentioned for context (real output filenames and counts from the oncology reference build) but weren't copied in, since they're generated data rather than source:

| File (not in repo) | Pipeline | Purpose |
|------|----------|---------|
| `remark-keyword-popup.mjs` | UI | Build-time MDX transform, word-level reverse index (mentioned in the reference build; not part of this pipeline's own output) |
| `keywords_v3.jsonl` | A | Usable Pipeline A output for a new textbook (start here if skipping the review layer) |
| `keywords_v5.jsonl` | A | Oncology's fully reviewed Wikipedia keyword set (8,049 entries) |
| `keywords_abbrevs_enriched.jsonl` | B | Oncology's enriched abbreviations (927 entries) |
| `keywords_combined.jsonl` | A+B | Oncology's final merged set (8,976 entries) |
