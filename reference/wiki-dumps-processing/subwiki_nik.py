#!/usr/bin/env python3

import sys
import re
import json
import argparse
from urllib.parse import quote

DEFAULT_CATEGORIES = [
    "Cancer",
    "Nanomedicine",
    "Nanotechnology",
    "Biotechnology",
]

PAGE_START_RE = re.compile(r"<page>")
PAGE_END_RE = re.compile(r"</page>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE)
TEXT_OPEN_RE = re.compile(r"<text\b[^>]*>", re.IGNORECASE)
TEXT_CLOSE_RE = re.compile(r"</text>", re.IGNORECASE)

CATEGORY_RE = re.compile(r"\[\[Category:([^\]|]+)", re.IGNORECASE)
IMAGE_RE = re.compile(r"\[\[(?:File|Image):([^|\]]+)", re.IGNORECASE)
SECTION_RE = re.compile(r"^\s*==[^=].*?==\s*$", re.MULTILINE)

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
REF_RE = re.compile(r"<ref\b[^>]*>.*?</ref>", re.IGNORECASE | re.DOTALL)
SELF_CLOSING_REF_RE = re.compile(r"<ref\b[^/]*/\s*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
MULTISPACE_RE = re.compile(r"\s+")


def normalize_title(value: str) -> str:
    return value.strip().replace("_", " ")


def build_source_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


def clean_lead_preserve_links(text: str) -> str:
    """
    Clean XML/HTML-like noise while preserving [[Wiki Links]].
    We do NOT flatten internal wiki links in script one.
    """
    text = COMMENT_RE.sub(" ", text)
    text = REF_RE.sub(" ", text)
    text = SELF_CLOSING_REF_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)

    # keep wiki links intact
    text = text.replace("'''", "")
    text = text.replace("''", "")

    text = MULTISPACE_RE.sub(" ", text).strip()
    return text


def extract_lead_text(raw_text: str) -> str:
    """
    Extract text before first section header like:
    == History ==
    == Applications ==
    """
    parts = SECTION_RE.split(raw_text, maxsplit=1)
    lead = parts[0] if parts else raw_text
    return clean_lead_preserve_links(lead)


def compile_target_categories(categories):
    normalized = {c.strip().lower() for c in categories if c.strip()}
    return normalized


def page_matches_categories(page_categories, target_categories):
    page_normalized = {c.strip().lower() for c in page_categories}
    return bool(page_normalized & target_categories)


def make_record(title, raw_text, target_categories):
    page_categories = CATEGORY_RE.findall(raw_text)

    if not page_matches_categories(page_categories, target_categories):
        return None

    lead = extract_lead_text(raw_text)
    matched_categories = sorted(
        {
            normalize_title(cat)
            for cat in page_categories
            if cat.strip().lower() in target_categories
        }
    )
    all_categories = sorted({normalize_title(cat) for cat in page_categories})
    images = sorted({img.strip() for img in IMAGE_RE.findall(raw_text) if img.strip()})

    return {
        "keyword": normalize_title(title),
        "definition_raw": lead,
        "source_url": build_source_url(normalize_title(title)),
        "images": images,
        "categories": matched_categories,
        "all_page_categories": all_categories,
    }


def process_dump(input_path, output_path, target_categories, progress_every):
    in_page = False
    in_text = False
    current_title = ""
    text_lines = []

    pages_seen = 0
    pages_written = 0

    import bz2
    open_fn = bz2.open if input_path.endswith(".bz2") else open
    with open_fn(input_path, "rt", encoding="utf-8", errors="ignore") as infile, \
         open(output_path, "w", encoding="utf-8") as outfile:

        for line in infile:
            if not in_page:
                if PAGE_START_RE.search(line):
                    in_page = True
                    in_text = False
                    current_title = ""
                    text_lines = []
                continue

            # inside page
            if not current_title:
                title_match = TITLE_RE.search(line)
                if title_match:
                    current_title = title_match.group(1)

            if not in_text:
                text_open_match = TEXT_OPEN_RE.search(line)
                if text_open_match:
                    in_text = True
                    after_open = line[text_open_match.end():]
                    text_close_match = TEXT_CLOSE_RE.search(after_open)

                    if text_close_match:
                        text_lines.append(after_open[:text_close_match.start()])
                        in_text = False
                    else:
                        text_lines.append(after_open)
            else:
                text_close_match = TEXT_CLOSE_RE.search(line)
                if text_close_match:
                    text_lines.append(line[:text_close_match.start()])
                    in_text = False
                else:
                    text_lines.append(line)

            if PAGE_END_RE.search(line):
                pages_seen += 1

                raw_text = "".join(text_lines)
                record = make_record(current_title, raw_text, target_categories)

                if record is not None:
                    outfile.write(json.dumps(record, ensure_ascii=False) + "\n")
                    pages_written += 1

                if progress_every > 0 and pages_seen % progress_every == 0:
                    print(
                        f"Processed pages: {pages_seen:,} | matched: {pages_written:,}",
                        file=sys.stderr
                    )

                in_page = False
                in_text = False
                current_title = ""
                text_lines = []

    print(f"Done. Pages processed: {pages_seen:,}", file=sys.stderr)
    print(f"Subwiki pages written: {pages_written:,}", file=sys.stderr)
    print(f"Output file: {output_path}", file=sys.stderr)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract a category-based subwiki from a Wikipedia XML dump."
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Path to Wikipedia XML dump file (uncompressed)"
    )
    parser.add_argument(
        "-o", "--output",
        default="subwiki.jsonl",
        help="Output JSONL file"
    )
    parser.add_argument(
        "-c", "--categories",
        nargs="*",
        default=None,
        help="Override default category list. Example: -c Biology Genetics Bioinformatics"
    )
    parser.add_argument(
        "--categories-file",
        default=None,
        help="Optional text file with one category per line"
    )
    parser.add_argument(
        "-v", "--view-every",
        type=int,
        default=10000,
        help="Print progress every N pages"
    )
    return parser.parse_args()


def load_categories(args):
    if args.categories_file:
        with open(args.categories_file, "r", encoding="utf-8") as f:
            categories = [line.strip() for line in f if line.strip()]
        if not categories:
            raise ValueError("Category file was provided but contained no categories.")
        return categories

    if args.categories:
        return args.categories

    return DEFAULT_CATEGORIES


def main():
    args = parse_args()
    categories = load_categories(args)
    target_categories = compile_target_categories(categories)

    print("Using categories:", ", ".join(categories), file=sys.stderr)

    process_dump(
        input_path=args.input,
        output_path=args.output,
        target_categories=target_categories,
        progress_every=args.view_every,
    )


if __name__ == "__main__":
    main()
    
'''

python subwiki.py -i enwiki-latest-pages-articles.xml -o oncology_subwiki.jsonl --categories-file categories.txt


'''    