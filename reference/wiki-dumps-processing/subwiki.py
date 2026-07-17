#!/usr/bin/env python3
"""
subwiki.py — Stream the English Wikipedia XML dump and extract domain-relevant pages.

Edit the three config values at the top, then run:
    python subwiki.py
"""

import bz2
import json
import logging
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — edit these
# ---------------------------------------------------------------------------

DUMP_PATH       = "enwiki-latest-pages-articles.xml.bz2"
CATEGORIES_FILE = "categories_clean.txt"
OUTPUT_FILE     = "oncology_subwiki.jsonl"
LOG_EVERY       = 10_000   # print progress every N pages

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XML state machine — streams one page at a time, O(1 page) memory
# ---------------------------------------------------------------------------

_RE_TITLE      = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_RE_NS         = re.compile(r"<ns>(\d+)</ns>")
_RE_TEXT_OPEN  = re.compile(r"<text[^>]*>")
_RE_TEXT_CLOSE = re.compile(r"</text>")
_RE_PAGE_OPEN  = re.compile(r"<page>")
_RE_PAGE_CLOSE = re.compile(r"</page>")
_RE_REDIRECT   = re.compile(r"<redirect\s", re.IGNORECASE)


def stream_pages(dump_path: str):
    """Yield one dict per main-namespace, non-redirect article page."""
    in_page = in_text = is_redirect = False
    title = ns = ""
    text_chunks = []

    open_fn = bz2.open if dump_path.endswith(".bz2") else open
    with open_fn(dump_path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not in_page:
                if _RE_PAGE_OPEN.search(line):
                    in_page = True
                    in_text = is_redirect = False
                    title = ns = ""
                    text_chunks = []
                continue

            if _RE_PAGE_CLOSE.search(line):
                if ns == "0" and title and not is_redirect:
                    yield {"title": title, "wikitext": "".join(text_chunks)}
                in_page = False
                continue

            if _RE_REDIRECT.search(line):
                is_redirect = True

            m = _RE_TITLE.search(line)
            if m and not title:
                title = m.group(1).strip()
                continue

            m = _RE_NS.search(line)
            if m and not ns:
                ns = m.group(1).strip()
                continue

            if not in_text:
                m = _RE_TEXT_OPEN.search(line)
                if m:
                    in_text = True
                    after = line[m.end():]
                    if _RE_TEXT_CLOSE.search(after):
                        text_chunks.append(_RE_TEXT_CLOSE.split(after)[0])
                        in_text = False
                    else:
                        text_chunks.append(after)
            else:
                if _RE_TEXT_CLOSE.search(line):
                    text_chunks.append(_RE_TEXT_CLOSE.split(line)[0])
                    in_text = False
                else:
                    text_chunks.append(line)

# ---------------------------------------------------------------------------
# Wikitext helpers
# ---------------------------------------------------------------------------

_RE_CATEGORY    = re.compile(r"\[\[Category:([^\]|]+?)(?:\|[^\]]*)?\]\]", re.IGNORECASE)
_RE_IMAGE       = re.compile(r"\[\[(?:File|Image):([^\]|]+?)(?:\|[^\]]*)?\]\]", re.IGNORECASE)
_RE_WIKILINK    = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]")
_RE_TEMPLATE    = re.compile(r"\{\{[^}]*\}\}", re.DOTALL)
_RE_REF         = re.compile(r"<ref[^>]*/?>.*?</ref>|<ref[^/]*/?>", re.DOTALL | re.IGNORECASE)
_RE_HTML_TAG    = re.compile(r"<[^>]+>")
_RE_BOLD_ITALIC = re.compile(r"'{2,3}")
_RE_HEADING     = re.compile(r"^=+[^=]+=+\s*$", re.MULTILINE)
_RE_MULTI_NL    = re.compile(r"\n{3,}")


def extract_categories(wikitext: str) -> list[str]:
    return [m.group(1).strip() for m in _RE_CATEGORY.finditer(wikitext)]


def extract_images(wikitext: str) -> list[str]:
    seen, result = set(), []
    for m in _RE_IMAGE.finditer(wikitext):
        name = m.group(1).strip()
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def extract_lead(wikitext: str, max_chars: int = 600) -> str:
    """Lead section (before first ==Heading==), markup stripped, truncated."""
    parts = re.split(r"\n==\s*[^=]", wikitext, maxsplit=1)
    plain = parts[0]

    plain = _RE_REF.sub("", plain)
    plain = _RE_TEMPLATE.sub("", plain)
    plain = _RE_WIKILINK.sub(r"\1", plain)
    plain = _RE_HTML_TAG.sub("", plain)
    plain = _RE_BOLD_ITALIC.sub("", plain)
    plain = _RE_HEADING.sub("", plain)
    plain = _RE_MULTI_NL.sub("\n\n", plain).strip()
    plain = " ".join(l for l in plain.splitlines() if l.strip())

    if len(plain) <= max_chars:
        return plain

    truncated = plain[:max_chars]
    last_period = truncated.rfind(". ")
    if last_period > max_chars // 2:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "…"


# ---------------------------------------------------------------------------
# Category matching — exact match against categories_clean.txt, plus a
# title-exact-match fallback (see title_matches() below)
# ---------------------------------------------------------------------------

def load_target_terms(path: str) -> set[str]:
    terms = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.add(line.lower())
    if not terms:
        sys.exit(f"ERROR: {path} is empty or missing.")
    log.info("Loaded %d category terms from %s", len(terms), path)
    return terms


def matched_categories(page_cats: list[str], target_terms: set[str]) -> list[str]:
    """
    Exact match against CATEGORIES_FILE — a page's category is kept only if
    it exactly equals one of the target terms (case-insensitive), not merely
    contains one as a substring.

    Substring matching was tried and rejected: it's a superset of exact
    matching (a term that IS a full category name still matches, trivially),
    but "superset of matches" isn't the same as "superset of correct
    matches" — the extra matches it pulls in are false positives, e.g. the
    target term "SERS" (a real nanotech category) matches inside the
    unrelated Wikipedia category "Composers" because "sers" is a substring
    of "composers". On the real dump this produced ~81K matches vs ~10K
    with exact matching, and the difference was mostly noise like that.

    This means CATEGORIES_FILE needs to contain full, exact Wikipedia
    category names (e.g. "Lung cancer", "Breast cancer", "Cancer research"),
    not short fragments (e.g. just "cancer") — a fragment will only match a
    page whose category is that exact word and nothing else, which is rare.
    """
    return [cat for cat in page_cats if cat.strip().lower() in target_terms]


def title_matches(title: str, target_terms: set[str]) -> bool:
    """
    Exact-match fallback, ported from subwiki_nik.py: catches a page whose
    title itself is exactly one of the target terms, even when the page's
    own Wikipedia categories don't line up cleanly with the topic list
    (e.g. a concept page whose categories are all more general than the
    concept itself). This is a genuinely separate signal from category
    matching above, not a substitute for it — a page can be caught by
    either check independently.
    """
    return title.strip().lower() in target_terms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    target_terms      = load_target_terms(CATEGORIES_FILE)
    pages_scanned     = 0
    pages_matched     = 0
    title_only_hits   = 0   # matched via title fallback, not via any category

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        for page in stream_pages(DUMP_PATH):
            pages_scanned += 1

            if pages_scanned % LOG_EVERY == 0:
                log.info("Scanned %d pages | matched %d", pages_scanned, pages_matched)

            title        = page["title"]
            all_cats     = extract_categories(page["wikitext"])
            matched_cats = matched_categories(all_cats, target_terms)
            title_hit    = title_matches(title, target_terms)

            if not matched_cats and not title_hit:
                continue
            if title_hit and not matched_cats:
                title_only_hits += 1

            record = {
                "keyword":          title,
                "definition_short": extract_lead(page["wikitext"]),
                "source_url":       "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                "images":           extract_images(page["wikitext"]),
                "categories":       matched_cats,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            pages_matched += 1

    log.info("Done. Scanned: %d | Matched: %d (of which %d via title-only fallback) | Output: %s",
             pages_scanned, pages_matched, title_only_hits, OUTPUT_FILE)


if __name__ == "__main__":
    main()
