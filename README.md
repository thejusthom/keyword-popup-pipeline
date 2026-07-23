# Keyword Popup Pipeline

Turns a Wikipedia XML dump plus your textbook's own MDX/Markdown files into a keyword popup database: hover a term, get a short definition; click it, get a full article. Works for a textbook on any subject — this folder ships with an oncology example filled in, but every subject-specific value lives in one file (`config.py`) so you can point it at a different textbook without touching the stage scripts.

**Getting started with a new subject? Paste your textbook's MDX files into a `docs/` folder and run Stage 0 first** (`00_config_generator/generate_config.py`) — it scans the textbook itself and drafts `config.py` and `categories.txt` for you, instead of you writing them by hand. See "Step 0" below.

This pipeline has been tested end-to-end with synthetic sample data (see "Verification" at the bottom). Two real bugs were found and fixed during that testing — see "Fixes made while building this" below.

**Related repo:** [keyword-qa-pipeline](https://github.com/JanviChitroda24/keyword-qa-pipeline) picks up where this one leaves off — a multi-model (Mistral + OpenAI + Claude) quality-review and consensus layer for both the keyword set and the abbreviation set, with a review dashboard and production export. This is the working implementation of the "optional multi-model review" mentioned throughout the docs here.

---

## Folder Layout

```
pipeline/
├── config.py                    ← THE FILE YOU EDIT for a new subject (or let Stage 0 draft it)
├── categories.txt                ← Wikipedia category list (oncology example, 468 terms)
├── requirements.txt
├── .env.example                  ← copy to .env and fill in your keys
├── 00_config_generator/
│   └── generate_config.py         Stage 0 — scan your textbook, draft config.py automatically
├── 01_wiki_extract/
│   ├── subwiki.py                 Stage 1 — stream the Wikipedia dump
│   └── subwiki.sbatch             SLURM template for cluster runs
├── 02_wiki_clean/
│   └── clean_wiki.py              Stage 2 — free, rule-based cleaning
├── 03_wiki_rewrite/
│   └── rewrite_wiki.py            Stage 3 — LLM rewrites every definition (the important one)
├── 04_abbrev_scan/
│   └── scan_abbrevs.py            Stage 4 — scan your textbook for abbreviations
├── 05_abbrev_enrich/
│   └── enrich_abbrevs.py          Stage 5 — LLM writes abbreviation definitions
├── 06_merge/
│   └── merge_keywords.py          Stage 6 — merge Wikipedia + abbreviation sets
├── 07_upload/
│   └── upload_to_supabase.py      Stage 7 — filter, export keywords.json, upload
├── reference/                     original oncology scripts this pipeline was generalized
│                                   from — kept as reference material, not part of the
│                                   pipeline itself (see CLAUDE_CODE_PROMPT.md and
│                                   KEYWORD_PIPELINE_DOCS.md for how they're used)
├── sample_data/                   synthetic test data used to verify this pipeline works
└── docs/                          your textbook's MDX/MD source (gitignored — add your own)
```

Each stage reads a file and writes a file — if stage 4 crashes, stages 1–3 don't need to be redone.

---

## Prerequisites

| Requirement | Why |
|---|---|
| Python 3.10+ | All scripts are Python |
| `pip install -r requirements.txt` | Installs the `openai` package (the only external dependency, needed for stages 3 and 5) |
| A Wikipedia XML dump | `https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles.xml.bz2` — large (~20GB compressed); a laptop can process it but slowly, a cluster is faster |
| An OpenAI API key | Stages 3 and 5 write definitions with an LLM — this is not optional for real output |
| A Supabase project | Stage 7 uploads to a `articles` table — free tier is enough to start |
| Your textbook's MDX/MD source files | Input for stage 0 (drafts your config) and stage 4 (finds abbreviations) |

No account is needed for Gene Ontology (stage 5's optional external lookup) — its public API is unauthenticated.

---

## Step-by-Step: Running the Full Pipeline

### 0. Set up

```bash
cd pipeline
pip install -r requirements.txt
cp .env.example .env
# edit .env: fill in OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
```

### 1. Stage 0 — paste your textbook, draft `config.py` automatically

Copy (or symlink) your textbook's MDX/MD source into a `docs/` folder somewhere the pipeline can read — the exact layout from your platform is fine: chapter folders full of numbered `.mdx` files plus a `meta.json` per chapter, e.g.

```
docs/
├── Chapter1/
│   ├── meta.json
│   ├── 1_Introduction_to_Cells.mdx
│   ├── 6_Tissue_Organization_And_Organ_Systems.mdx
│   └── 7_Cell_Death_Apoptosis_and_Necrosis.mdx
├── Chapter2/
│   ├── meta.json
│   └── ...
└── AppendixB/
    └── ...
```

Then run Stage 0:

```bash
cd 00_config_generator
python generate_config.py --docs ../docs --dry-run   # free smoke test — no API call
python generate_config.py --docs ../docs             # real run — one LLM call, reads your whole textbook
```

This scans every `.mdx`/`.md` file plus every `meta.json`, and also runs the same abbreviation-frequency scan Stage 4 uses later (so `TP53`, `VEGF`, `TME`, whatever your textbook actually uses shows up as evidence, not guesswork). It sends that scan to an LLM and gets back a complete draft: `config.generated.py`, `categories.generated.txt`, and `few_shot_examples.generated.txt`.

**Output:** `config.generated.py`, `categories.generated.txt`, `few_shot_examples.generated.txt`, `textbook_scan_cache.json` (Stage 4 reuses this later so it doesn't have to re-scan the same files).

**No script, no problem:** you can skip running this file entirely and just paste your textbook's MDX content directly into a chat with an LLM instead, asking it to fill in the same `config.py` template — see the module docstring at the top of `generate_config.py` for the exact prompt to use. The script just automates that same conversation and reads the whole textbook instead of a pasted excerpt.

### 2. Review the draft, then point the pipeline at it

Open `config.generated.py` next to the real `config.py` and read through it — the LLM is a strong first draft, not a final answer. Check especially:
- `DOMAIN_NAME`, `DOMAIN_DESCRIPTION`, `TEXTBOOK_PLATFORM_NAME` read correctly
- `CATEGORY_LIST` / `CATEGORY_KEYWORDS` match how your textbook actually organizes topics
- `wikipedia_categories` (which becomes `categories.generated.txt`) looks like real, full, exact Wikipedia category names for your subject (e.g. `"Lung cancer"`, not just `"cancer"`) — Stage 1 matches these exactly, not as substrings, so vague or truncated entries will silently under-match

Once you're happy with it, either:
- run `python generate_config.py --docs ../docs --apply` to write straight over `../config.py` and `../categories.txt`, or
- rename the `.generated.*` files yourself (`mv config.generated.py ../config.py`, etc.)

**One manual step Stage 0 can't fully do for you:** `03_wiki_rewrite/rewrite_wiki.py` needs 4 worked examples (`_FEW_SHOT`) hardcoded directly in that file, teaching the LLM what good output looks like. Stage 0 drafts these too (`few_shot_examples.generated.txt`) — open it and copy the 4 blocks into `rewrite_wiki.py` by hand, replacing the existing `_FEW_SHOT` string. The module docstring at the top of that file explains what each example should demonstrate.

(Prefer to skip Stage 0 and write `config.py` by hand instead? Every field is documented with inline comments in `config.py` itself — same result, more typing.)

### 3. Stage 1 — extract from Wikipedia

```bash
cd ../01_wiki_extract
# edit DUMP_PATH at the top of subwiki.py to point at your downloaded dump
python subwiki.py
```

On a laptop this can take hours depending on dump size; on a cluster, submit `subwiki.sbatch` (edit the account/path placeholders in it first) via `sbatch subwiki.sbatch`.

**Output:** `subwiki.jsonl`

### 4. Stage 2 — clean (free, no API calls)

```bash
cd ../02_wiki_clean
python clean_wiki.py --input ../01_wiki_extract/subwiki.jsonl
```

**Output:** `keywords_clean.jsonl`. Check the printed relevant/irrelevant counts — if too many obviously-wrong entries are marked relevant, revisit `config.py`'s `NON_BIO_CONTENT_SIGNALS` / `NON_BIO_CAT_SIGNALS` (Stage 3 catches most of what slips through here, so don't over-tune this step).

### 5. Stage 3 — LLM rewrite (costs money — test cheaply first)

```bash
cd ../03_wiki_rewrite
python rewrite_wiki.py --dry-run --limit 5     # free smoke test — no API calls
python rewrite_wiki.py --limit 5                # real test, 5 entries, ~$0.005-0.08
python rewrite_wiki.py                          # full run — script prints a cost estimate first
```

Use `--resume` if a run is interrupted. Entries needing a human look land in `flagged_for_review.jsonl`.

**Output:** `keywords_v3.jsonl`

### 6. Stage 4 — scan your textbook for abbreviations

```bash
cd ../04_abbrev_scan
python scan_abbrevs.py --wiki-output ../03_wiki_rewrite/keywords_v3.jsonl
```

If Stage 0's `textbook_scan_cache.json` is present, this reuses it instead of re-walking every MDX file a second time (pass `--no-cache` to force a fresh scan, e.g. if the textbook changed since Stage 0 ran).

**Output:** `keywords_abbrevs.jsonl` + `scan_report.csv`. Open the CSV and sort by `status` — `missing` rows are what Stage 5 will define; `skipped_noise` rows are worth a skim to make sure nothing real got excluded (extend `config.ABBREV_SKIP` if so).

### 7. Stage 5 — LLM writes abbreviation definitions

```bash
cd ../05_abbrev_enrich
python enrich_abbrevs.py --dry-run --limit 5    # free smoke test
python enrich_abbrevs.py                        # full run
```

Checkpoints every 25 entries — safe to re-run if interrupted.

**Output:** `keywords_abbrevs_enriched.jsonl`

### 8. Stage 6 — merge

```bash
cd ../06_merge
python merge_keywords.py
```

**Output:** `keywords_combined.jsonl`

### 9. Stage 7 — upload

```bash
cd ../07_upload
python upload_to_supabase.py --dry-run                                        # writes keywords.json only, no upload
python upload_to_supabase.py --output /path/to/your/nextjs-project/data/keywords.json  # real upload + writes keywords.json for Next.js
```

Point `--output` at wherever your Next.js (or other frontend) project actually lives — it's a separate project from this pipeline, so the path depends entirely on your own setup.

Retries failed batches automatically (SSL errors are common on large uploads and are transient — re-running the whole script is always safe, it upserts on `slug`).

---

## Fixes Made While Building This

Two real bugs were found and fixed while assembling and testing this pipeline from the original scripts:

1. **`clean_wiki.py` (Stage 2) — corrupted regex crashed on real text.** The original `process_keywords_v2.py` had a malformed character class, `[^\w\s-￿]*` (a stray Unicode character had corrupted the pattern), which threw `re.error: bad character range` on any input. Fixed to `[^\w]*`. Also: the original file was truncated mid-function in this project folder (`process_entry()` cut off after a single character, with no `main()` at all) — this version has both fully reconstructed and verified against the real `keywords_clean.jsonl` schema already in use downstream.

2. **`scan_abbrevs.py` (Stage 4) — hardcoded absolute paths and a required oncology-only dependency.** The original `build_all_abbrevs.py` had `/sessions/...` paths hardcoded for one specific machine, and unconditionally imported 4 files of hand-curated oncology abbreviation definitions (`abbrev_defs.py` through `abbrev_defs4.py`) that don't exist for a new subject. This version uses relative paths and makes hand-curated definitions fully optional (`config.ABBREV_DEFS_MODULE = None` by default) — every abbreviation just goes straight to Stage 5 for LLM enrichment unless you choose to hand-curate some yourself.

One **known limitation, not fixed** (by design — same tradeoff as the Wikipedia category matching):

3. **Abbreviation category inference uses substring matching**, so short patterns can collide with unrelated abbreviations — e.g. `CTL` gets categorized as "Medical Imaging" because it contains the substring `CT`. This doesn't affect the definition text (Stage 5's LLM writes that from context regardless), only the `category` label. If this matters for your use case, review `scan_report.csv` after Stage 4 and adjust `config.ABBREV_CATEGORY_RULES` ordering/specificity, or accept the occasional mislabel — it's the same "loose matching over-catches sometimes, tighten the list rather than the algorithm" tradeoff documented throughout this pipeline.

3 (minor, cosmetic) also fixed: `merge_keywords.py`'s "next step" hint used to print a garbled double-relative path when a non-default `--output` was passed; now prints the resolved absolute path.

4. **Stage 4 now reuses Stage 0's textbook scan instead of re-scanning MDX twice.** Both stages walk the same `.mdx`/`.md` files looking for abbreviations. `scan_abbrevs.py` checks for Stage 0's `textbook_scan_cache.json` first and reuses it automatically; `--no-cache` forces a fresh scan if the textbook changed since Stage 0 ran.

5. **`subwiki.py` (Stage 1) category matching was switched from substring to exact match, and a title-exact-match fallback was added.** This one needs unpacking, because it reverses something this README previously said:

   - The version originally built here matched categories by substring (`"cancer" in cat.lower()`), on the theory that substring matching is a superset of exact matching and therefore strictly safer. That reasoning was wrong for a curated glossary: substring matching also catches false positives. The concrete, real example: the target term `SERS` (a real nanotech category) matched inside the unrelated Wikipedia category `"Composers"`, because `"sers"` is a substring of `"composers"`. On the real reference dump this produced ~81K matches versus ~10K with exact matching, and the ~71K difference was mostly noise like that.
   - `matched_categories()` now does exact, case-insensitive set matching instead — a page's category is kept only if it *equals* a target term, not merely contains one. This requires `categories.txt` to list full, exact Wikipedia category names (`"Lung cancer"`, `"Cancer research"`) rather than short fragments (`"cancer"`) — the fragment-style category lists currently in this project (`categories.txt`, `wiki-dumps-processing/categories_clean.txt`) were built for the old substring approach and will under-match if used as-is with exact matching. Building a proper exact-match list means enumerating each category variant, not relying on a root word to auto-expand.
   - Separately, a title-exact-match fallback (ported from an earlier draft script, `subwiki_nik.py`, since retired) was added: a page is also kept if its own title exactly equals a target term, even when none of its categories do. This is a genuinely additive signal, independent of the category check.

   Verified with a synthetic dump (target terms `"cancer"`, `"sers"`): a page whose category was exactly `"SERS"` was correctly kept via exact category match; a page in the unrelated category `"Composers"` was correctly rejected (confirming the false positive is gone); a page titled exactly `"Cancer"` with only unrelated categories was correctly kept via the title fallback; a page whose only category was `"Lung cancer"` (not exactly `"cancer"`) was correctly *not* matched — demonstrating the real recall cost of exact matching against a fragment-style category list, exactly as described above; and redirects/talk pages continued to be skipped correctly.

---

## Verification

Every stage was run end-to-end against synthetic sample data (`sample_data/`) as part of building this folder — a 5-page fake Wikipedia XML dump (including a talk page and a redirect, to verify both are correctly skipped) and two tiny MDX chapter files with real abbreviations. Results:

- **Stage 0** (`--dry-run`) correctly scanned a synthetic 2-file, 1-chapter textbook (including a `meta.json`), found candidate abbreviations, and wrote valid, `py_compile`-clean `config.generated.py` — including a stress test with apostrophes and double-quotes embedded in generated field values, to confirm the `repr()`-based code generation can't produce broken Python
- **Stage 1** correctly matched 3 of 5 fake pages, correctly skipped the talk page (`ns=1`) and the redirect
- **Stage 2** correctly cleaned markup and assigned categories (after the regex fix above)
- **Stage 3** (`--dry-run`) correctly ran the full retry/quality-check/flagging loop with zero API calls or the `openai` package even installed
- **Stage 4** correctly found 11 candidate abbreviations, correctly filtered noise, correctly identified that `VEGF` (abbreviation) is a different keyword than "Vascular endothelial growth factor" (Wikipedia's full-name article) — confirming the exact gap Pipeline B exists to fill
- **Stage 5** (`--dry-run`) correctly built a context index from the MDX files and ran the checkpoint/resume logic
- **Stage 6** correctly merged 3 Wikipedia + 3 abbreviation entries into 6, with zero incorrect collisions
- **Stage 7** (`--dry-run`) correctly filtered, slugged, and wrote a valid `keywords.json`

All 9 Python files pass `py_compile` with no syntax errors.

**Not verified (requires real credentials/cost, left for you to test):** an actual OpenAI API call in stages 3/5, and an actual Supabase upload in stage 7. The `--dry-run` flags exist specifically so you can verify everything else works before spending money or touching a real database — run those first on a small `--limit` before committing to a full run.
