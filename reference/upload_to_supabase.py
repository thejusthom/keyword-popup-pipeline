#!/usr/bin/env python3
"""
upload_to_supabase.py — Upload keywords_v4.jsonl (approved entries) to Supabase
and write keywords.json for the Next.js popup component.

Pipeline:
    keywords_v4.jsonl → keywords.json  (popup component)
                     → Supabase articles table  (glossary pages)

Entries skipped automatically:
    • relevant=false  — irrelevant terms (books, geography, bios, disambiguation)
    • definition_short empty or <20 chars
    • non-biomedical content signals in definition text
    • pure acronym terms (≤10 alpha chars, all-caps) that collide with prose words

Optional:
    --skip-flagged    also skip entries tagged _thin_source or _retry_exhausted

NO EXTERNAL DEPENDENCIES — uses only Python stdlib (urllib).
Works on Python 3.10+ including 3.14 on Windows without any C compiler.

Usage:
    # Set env vars (recommended):
    set SUPABASE_URL=https://xxxx.supabase.co
    set SUPABASE_SERVICE_KEY=eyJ...   # use service role key, not anon key

    python upload_to_supabase.py

    # Or pass credentials as flags:
    python upload_to_supabase.py ^
        --input keywords_v4.jsonl ^
        --supabase-url https://xxxx.supabase.co ^
        --supabase-key eyJ... ^
        --output ./data/keywords.json ^
        --batch-size 500

    # Conservative: skip entries flagged for human review:
    python upload_to_supabase.py --skip-flagged

    # Dry run (writes keywords.json, skips Supabase upload):
    python upload_to_supabase.py --dry-run

    # Skip keywords.json generation (upload only):
    python upload_to_supabase.py --no-json
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env loader (stdlib only — no python-dotenv needed)
# ---------------------------------------------------------------------------

def load_dotenv(env_path: str = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ."""
    candidates = [
        env_path,
        Path(__file__).parent / ".env",
        Path.cwd() / ".env",
    ]
    for p in candidates:
        if p and Path(p).exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key   = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        os.environ[key] = value
            log.info("Loaded .env from %s", p)
            return

load_dotenv()


# ---------------------------------------------------------------------------
# Content-based false-positive filter
# ---------------------------------------------------------------------------

NON_BIOMEDICAL_SIGNALS = [
    # Real estate / urban development
    "mixed-use development", "mixed use development", "development complex",
    "square feet", "million square feet", "downtown cleveland", "downtown chicago",
    "development project", "real estate", "residential tower", "office space",
    "lead developer", "planning commission",
    # Biographical / person entries — more specific to avoid false positives
    "was an american", "was a british", "was an english", "was a french",
    "was a german", "was an italian", "was a canadian", "was an australian",
    "was a writer", "was a poet", "was a composer", "was a musician",
    "was a painter", "was a philosopher", "was an actor", "was a director",
    "was a politician", "was a novelist", "was a filmmaker",
    "was born in", "who died in", "| birth_date", "| death_date",
    # Geography / places
    "is a city in", "is a town in", "is a village in", "is a municipality",
    "is a county", "is located in", "united states census",
    # Space / astronomy — specific enough to avoid catching "satellite DNA" etc.
    "international space station", "european space agency", "outer space",
    "space shuttle", "nasa ", "spacecraft", "communications satellite",
    "weather satellite", "astronomical object", "astrophysic", "astrobiology",
    # Sports / entertainment — more specific
    "is a professional football", "is a professional basketball",
    "is a professional baseball", "is an american football team",
    "is a football club", "is a cricket",
    # Military / weapons
    "is a military", "is a weapon", "is a missile", "is a warship",
    "is an aircraft", "is a fighter jet",
]

def is_content_biomedical(definition: str) -> bool:
    check = definition[:400].lower()
    for signal in NON_BIOMEDICAL_SIGNALS:
        if signal in check:
            return False
    return True


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def make_slug(keyword: str) -> str:
    slug = keyword.lower()
    slug = slug.replace("(", "").replace(")", "")
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Load + transform
# ---------------------------------------------------------------------------

def load_entries(input_path: str, skip_flagged: bool = False) -> list[dict]:
    """
    Read the processed JSONL and return rows ready for Supabase insertion.

    Filters out:
      - relevant=false  (irrelevant terms — books, bios, geography, disambiguation)
      - definition_short empty or <20 chars
      - non-biomedical content signals
      - pure all-caps acronyms ≤10 chars
      - (if --skip-flagged) entries with _thin_source or _retry_exhausted tags

    Deduplicates by slug — keeps the entry with the longer definition on collision.
    """
    by_slug: dict[str, dict] = {}
    skipped_irrelevant = 0
    skipped_flagged    = 0
    skipped_empty      = 0
    total = 0

    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            total += 1

            # 1. Relevance flag — set by process_keywords_v3.py
            if not record.get("relevant", True):
                skipped_irrelevant += 1
                continue

            # 2. Low-confidence entries (thin source / retries exhausted)
            if skip_flagged and (record.get("_thin_source") or record.get("_retry_exhausted")):
                skipped_flagged += 1
                continue

            term       = record.get("keyword", "").strip()
            definition = record.get("definition_short", "").strip()
            full_text  = record.get("definition_raw", "").strip()
            category   = record.get("category", "Biomedical Science")
            source_url = record.get("source_url", "")

            # 3. Empty / too-short definition
            if len(definition) < 20:
                skipped_empty += 1
                continue

            # 4. Content-based false-positive check
            if not is_content_biomedical(definition):
                log.debug("Non-biomedical content filtered: %s", term)
                skipped_empty += 1
                continue

            slug = make_slug(term)
            if not slug:
                skipped_empty += 1
                continue

            entry = {
                "slug":       slug,
                "term":       term,
                "definition": definition,
                "full_text":  full_text or definition,
                "category":   category,
                "source_url": source_url,
            }

            # Slug collision — keep richer definition
            if slug in by_slug:
                existing = by_slug[slug]
                if len(definition) > len(existing["definition"]):
                    log.debug("Slug collision '%s': replacing '%s' with '%s'",
                              slug, existing["term"], term)
                    by_slug[slug] = entry
                else:
                    log.debug("Slug collision '%s': keeping '%s', dropping '%s'",
                              slug, existing["term"], term)
            else:
                by_slug[slug] = entry

    entries = list(by_slug.values())
    dupes = total - skipped_irrelevant - skipped_flagged - skipped_empty - len(entries)

    log.info("─" * 50)
    log.info("Total records:           %d", total)
    log.info("Skipped (irrelevant):    %d", skipped_irrelevant)
    if skip_flagged:
        log.info("Skipped (flagged):       %d", skipped_flagged)
    log.info("Skipped (empty/filtered):%d", skipped_empty)
    log.info("Slug duplicates dropped: %d", dupes)
    log.info("Valid entries to upload: %d", len(entries))
    log.info("─" * 50)
    return entries


# ---------------------------------------------------------------------------
# keywords.json — popup component data
# ---------------------------------------------------------------------------

def write_keywords_json(entries: list[dict], output_path: str) -> None:
    """Write keywords.json keyed by slug for KeywordPopup.tsx."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    keyed = {
        e["slug"]: {
            "term": e["term"],
        }
        for e in entries
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(keyed, fh, indent=2, ensure_ascii=False)

    size_kb = Path(output_path).stat().st_size / 1024
    log.info("Wrote keywords.json: %d entries (%.0f KB) → %s", len(keyed), size_kb, output_path)


# ---------------------------------------------------------------------------
# Supabase upload — stdlib only
# ---------------------------------------------------------------------------

def _do_request(req: urllib.request.Request, timeout: int = 60) -> int:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def upload_to_supabase(
    entries: list[dict],
    supabase_url: str,
    supabase_key: str,
    batch_size: int = 500,
    table: str = "articles",
) -> None:
    """Upsert entries into Supabase via PostgREST. No external packages needed."""
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/{table}?on_conflict=slug"
    headers = {
        "apikey":        supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=minimal",
    }

    total         = len(entries)
    total_batches = (total + batch_size - 1) // batch_size
    uploaded      = 0
    errors        = 0

    for i in range(0, total, batch_size):
        batch     = entries[i : i + batch_size]
        batch_num = i // batch_size + 1
        log.info("Uploading batch %d/%d (%d rows)…", batch_num, total_batches, len(batch))

        body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
        req  = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

        success = False
        for attempt in range(1, 4):
            try:
                status = _do_request(req)
                if status not in (200, 201, 204):
                    log.warning("Batch %d: unexpected status %d", batch_num, status)
                uploaded += len(batch)
                success = True
                break
            except urllib.error.HTTPError as e:
                body_text = e.read().decode("utf-8", errors="replace")
                log.error("Batch %d HTTP %d: %s", batch_num, e.code, body_text[:300])
                break
            except Exception as e:
                log.warning("Batch %d attempt %d failed: %s: %s",
                            batch_num, attempt, type(e).__name__, e)
                if attempt < 3:
                    time.sleep(attempt * 2)
                else:
                    log.error("Batch %d gave up after 3 attempts.", batch_num)

        if not success:
            errors += len(batch)

    log.info("Upload complete. Upserted: %d | Errors: %d", uploaded, errors)
    if errors:
        log.warning("%d rows failed — re-run to retry (upsert is safe to repeat).", errors)


# ---------------------------------------------------------------------------
# Prune — delete rows in Supabase not present in the local JSONL
# ---------------------------------------------------------------------------

def fetch_all_slugs(supabase_url: str, supabase_key: str, table: str = "articles") -> set:
    """Fetch every slug currently in the Supabase table (handles pagination)."""
    headers = {
        "apikey":        supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Accept":        "application/json",
        "Range-Unit":    "items",
    }
    slugs   = set()
    offset  = 0
    page    = 1000   # rows per page

    while True:
        url = (
            f"{supabase_url.rstrip('/')}/rest/v1/{table}"
            f"?select=slug&limit={page}&offset={offset}"
        )
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = json.loads(resp.read().decode("utf-8"))

        if not rows:
            break

        for row in rows:
            slugs.add(row["slug"])

        if len(rows) < page:
            break   # last page
        offset += page

    return slugs


def prune_supabase(
    local_slugs: set,
    supabase_url: str,
    supabase_key: str,
    table: str = "articles",
    batch_size: int = 500,
    dry_run: bool = False,
) -> None:
    """Delete rows from Supabase whose slugs are not in local_slugs."""
    log.info("Fetching all slugs from Supabase table '%s'…", table)
    remote_slugs = fetch_all_slugs(supabase_url, supabase_key, table)
    log.info("  Remote slugs: %d", len(remote_slugs))
    log.info("  Local slugs:  %d", len(local_slugs))

    stale = sorted(remote_slugs - local_slugs)
    log.info("  Stale rows to delete: %d", len(stale))

    if not stale:
        log.info("Nothing to prune — Supabase is already in sync.")
        return

    if dry_run:
        log.info("Dry run — skipping delete. Would remove:")
        for s in stale[:20]:
            log.info("  - %s", s)
        if len(stale) > 20:
            log.info("  ... and %d more", len(stale) - 20)
        return

    headers = {
        "apikey":        supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    deleted = 0
    errors  = 0

    for i in range(0, len(stale), batch_size):
        batch     = stale[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_b   = (len(stale) + batch_size - 1) // batch_size

        # PostgREST: DELETE /table?slug=in.(a,b,c,...)
        # URL-encode the slug list so non-ASCII characters (é, ö, etc.) don't break the request
        slug_list = ",".join(batch)
        encoded   = urllib.parse.quote(f"in.({slug_list})", safe="(),")
        url = f"{supabase_url.rstrip('/')}/rest/v1/{table}?slug={encoded}"
        req = urllib.request.Request(url, headers=headers, method="DELETE")

        log.info("Deleting batch %d/%d (%d rows)…", batch_num, total_b, len(batch))
        for attempt in range(1, 4):
            try:
                _do_request(req)
                deleted += len(batch)
                break
            except urllib.error.HTTPError as e:
                body_text = e.read().decode("utf-8", errors="replace")
                log.error("Delete batch %d HTTP %d: %s", batch_num, e.code, body_text[:300])
                errors += len(batch)
                break
            except Exception as e:
                log.warning("Delete batch %d attempt %d failed: %s", batch_num, attempt, e)
                if attempt < 3:
                    time.sleep(attempt * 2)
                else:
                    log.error("Delete batch %d gave up.", batch_num)
                    errors += len(batch)

    log.info("Prune complete. Deleted: %d | Errors: %d", deleted, errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload processed keyword JSONL to Supabase and write keywords.json.",
    )
    p.add_argument(
        "--input",
        default="keywords_v4.jsonl",
        help="Path to processed JSONL file (default: keywords_v4.jsonl)",
    )
    p.add_argument(
        "--output",
        default="./data/keywords.json",
        help="Output path for keywords.json (default: ./data/keywords.json)",
    )
    p.add_argument(
        "--supabase-url",
        default=os.getenv("SUPABASE_URL"),
        help="Supabase project URL (or set SUPABASE_URL env var)",
    )
    p.add_argument(
        "--supabase-key",
        default=os.getenv("SUPABASE_SERVICE_KEY"),
        help="Supabase service role key (or set SUPABASE_SERVICE_KEY env var)",
    )
    p.add_argument(
        "--table",
        default="articles",
        help="Supabase table name (default: articles)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per upload batch (default: 500)",
    )
    p.add_argument(
        "--skip-flagged",
        action="store_true",
        help="Also skip entries tagged _thin_source or _retry_exhausted (conservative upload)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate keywords.json but skip Supabase upload",
    )
    p.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing keywords.json (upload only)",
    )
    p.add_argument(
        "--preview",
        type=int,
        default=0,
        metavar="N",
        help="Print N sample entries to stdout for inspection",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="Delete rows from Supabase whose slugs are not in the local JSONL",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    entries = load_entries(args.input, skip_flagged=args.skip_flagged)

    if not entries:
        log.error("No valid entries found. Check your input file.")
        sys.exit(1)

    if args.preview > 0:
        print("\n--- Sample entries ---")
        for entry in entries[: args.preview]:
            print(json.dumps(entry, indent=2, ensure_ascii=False))
            print()

    if not args.no_json:
        write_keywords_json(entries, args.output)

    if args.dry_run:
        log.info("Dry run — Supabase upload skipped.")
        return

    if args.supabase_url and args.supabase_key:
        upload_to_supabase(
            entries, args.supabase_url, args.supabase_key,
            args.batch_size, args.table,
        )
        if args.prune:
            local_slugs = {e["slug"] for e in entries}
            prune_supabase(
                local_slugs, args.supabase_url, args.supabase_key,
                args.table, args.batch_size, dry_run=False,
            )
    else:
        log.error(
            "No Supabase credentials provided.\n"
            "Set SUPABASE_URL + SUPABASE_SERVICE_KEY env vars,\n"
            "or pass --supabase-url and --supabase-key."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
