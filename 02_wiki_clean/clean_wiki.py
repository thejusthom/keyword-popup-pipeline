#!/usr/bin/env python3
"""
clean_wiki.py — Stage 2: rule-based cleaning, no LLM cost.

Reads Stage 1's raw JSONL, strips wiki markup, assigns a category, filters
obvious irrelevant content, and builds a rough extractive definition_short.
Entirely offline — no API calls, no cost.

Usage:
    python clean_wiki.py                                   # uses defaults below
    python clean_wiki.py --input ../01_wiki_extract/subwiki.jsonl --output keywords_clean.jsonl
    python clean_wiki.py --limit 20                         # smoke-test on the first 20 entries

All subject-specific lists (category map, keyword scoring, relevance red
flags) live in ../config.py — edit that file, not this one, to point at a
new textbook subject.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

# ── 1. MARKUP CLEANER ─────────────────────────────────────────────────────────

def remove_html_comments(text):
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'&lt;!--.*?--&gt;', '', text, flags=re.DOTALL)
    return text

def remove_templates(text):
    for _ in range(10):
        prev = text
        text = re.sub(r'\{\{[^{}]*\}\}', '', text)
        if text == prev:
            break
    text = re.sub(r'\{\{.*?\}\}', '', text, flags=re.DOTALL)
    text = text.replace('{{', '').replace('}}', '')
    return text

def remove_file_links(text):
    for _ in range(5):
        prev = text
        text = re.sub(
            r'\[\[(?:File|Image|Media):[^\[\]]*(?:\[\[[^\[\]]*\]\][^\[\]]*)*\]\]',
            '', text, flags=re.IGNORECASE)
        if text == prev:
            break
    text = re.sub(r'\[\[(?:File|Image|Media):[^\]]*(?:\](?!\]))?', '', text, flags=re.IGNORECASE)
    return text

def convert_wikilinks(text):
    text = re.sub(r'\[\[(?:[^\[\]|]*\|)([^\[\]|]*)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\[\]|]*)\]\]', r'\1', text)
    text = text.replace('[[', '').replace(']]', '')
    return text

def decode_html_entities(text):
    reps = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>', '&nbsp;': ' ', '&quot;': '"',
        '&#160;': ' ', '&mdash;': '—', '&ndash;': '–', '&hellip;': '…', '&times;': '×',
        '&alpha;': 'α', '&beta;': 'β', '&gamma;': 'γ', '&delta;': 'δ', '&sigma;': 'σ',
        '&mu;': 'μ', '&#39;': "'", '&apos;': "'", '&lsquo;': "'", '&rsquo;': "'",
        '&ldquo;': '"', '&rdquo;': '"', '&minus;': '−', '&plusmn;': '±', '&deg;': '°',
        '&sup2;': '²', '&sup3;': '³', '&prime;': '′', '&isin;': '∈', '&rarr;': '→',
        '&asymp;': '≈', '&le;': '≤', '&ge;': '≥', '&ne;': '≠', '&infin;': '∞',
        '&kappa;': 'κ', '&lambda;': 'λ', '&omega;': 'ω', '&pi;': 'π', '&rho;': 'ρ',
        '&tau;': 'τ', '&upsilon;': 'υ', '&phi;': 'φ', '&chi;': 'χ', '&psi;': 'ψ',
        '&Sigma;': 'Σ', '&Delta;': 'Δ', '&Omega;': 'Ω', '&theta;': 'θ', '&eta;': 'η',
        '&epsilon;': 'ε', '&zeta;': 'ζ', '&shy;': '',
    }
    for ent, ch in reps.items():
        text = text.replace(ent, ch)
    try:
        text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    except Exception:
        pass
    text = re.sub(r'&[a-zA-Z]{2,10};', '', text)
    return text

def remove_refs(text):
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    text = re.sub(r'</ref>', '', text)
    text = re.sub(r'<ref[^>]*>', '', text)
    return text

def remove_html_tags(text):
    return re.sub(r'<[^>]{0,60}>', '', text)

def remove_category_links(text):
    return re.sub(r'\[\[Category:[^\]]*\]\]', '', text, flags=re.IGNORECASE)

def remove_external_links(text):
    text = re.sub(r'\[https?://\S+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://\S+\]', '', text)
    return text

def remove_image_artifacts(text):
    text = re.sub(r'\b\d+(?:x\d+)?px\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'thumb\|', '', text)
    text = re.sub(r'upright=[\d.]+\|?', '', text)
    text = re.sub(r'(?:left|right|center|none)\|', '', text)
    text = re.sub(r'alt=[^|\n]+\|', '', text)
    return text

def remove_wiki_formatting(text):
    text = re.sub(r"'{2,3}", '', text)
    text = re.sub(r'^-{4,}$', '', text, flags=re.MULTILINE)
    return text

def clean_whitespace(text):
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == '':
            clean_lines.append('')
        elif len(stripped) > 10 and not re.fullmatch(r'[^\w]*', stripped):
            clean_lines.append(stripped)
    text = '\n'.join(clean_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def clean_wikipedia_markup(text: str) -> str:
    if not text:
        return ''
    text = remove_html_comments(text)
    text = remove_templates(text)
    text = decode_html_entities(text)
    text = remove_refs(text)
    text = remove_file_links(text)
    text = remove_html_tags(text)
    text = remove_category_links(text)
    text = remove_external_links(text)
    text = convert_wikilinks(text)
    text = remove_image_artifacts(text)
    text = remove_wiki_formatting(text)
    text = clean_whitespace(text)
    return text

# ── 2. PARAGRAPH UTILS ────────────────────────────────────────────────────────

def split_into_sentences(text):
    text = re.sub(r'\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|vs|etc|Fig|i\.e|e\.g|cf|approx|No|Vol|pp|ed|et al)\.\s', r'\1@@@ ', text)
    text = re.sub(r'\b([A-Z]{1,2})\.\s', r'\1@@@ ', text)
    sents = re.split(r'(?<=[.!?])\s+', text)
    return [s.replace('@@@', '.').strip() for s in sents if len(s.strip()) > 5]

def ensure_two_paragraphs(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r'\n\n+', text) if p.strip()]
    if len(paragraphs) >= 2:
        return '\n\n'.join(paragraphs)
    if paragraphs:
        sents = split_into_sentences(paragraphs[0])
        if len(sents) >= 4:
            mid = max(2, len(sents) // 2)
            return ' '.join(sents[:mid]) + '\n\n' + ' '.join(sents[mid:])
        elif len(sents) >= 2:
            return sents[0] + '\n\n' + ' '.join(sents[1:])
    return text

def make_definition_short(keyword: str, cleaned_text: str) -> str:
    if not cleaned_text:
        return ''
    first_para = re.split(r'\n\n', cleaned_text)[0]
    sents = split_into_sentences(first_para)
    if not sents:
        return ''
    kw_lower = re.sub(r'^category:', '', keyword.lower()).strip()
    start_idx = 0
    for i, s in enumerate(sents[:4]):
        if kw_lower[:8] in s.lower():
            start_idx = i
            break
    selected, char_count = [], 0
    for s in sents[start_idx:start_idx + 4]:
        if char_count + len(s) > 500 and len(selected) >= 2:
            break
        selected.append(s)
        char_count += len(s)
        if len(selected) >= 3:
            break
    return ' '.join(selected)

# ── 3. CATEGORY ASSIGNMENT (uses config.py) ──────────────────────────────────

def assign_category(keyword, categories, all_page_cats, cleaned_text):
    kw_lower = keyword.lower()
    wiki_cats_lower = [c.lower() for c in (categories + all_page_cats)]

    for wc in wiki_cats_lower:
        for key, cat in config.WIKI_CAT_MAP.items():
            if key in wc:
                return cat

    probe = (kw_lower + ' ' + ' '.join(wiki_cats_lower) + ' ' + cleaned_text[:600].lower())
    scores = {cat: 0 for cat in config.CATEGORY_LIST}
    for cat in config.CATEGORY_LIST:
        for pattern in config.CATEGORY_KEYWORDS.get(cat, []):
            if pattern in probe:
                w = 1
                if pattern in kw_lower:
                    w += 4
                if any(pattern in wc for wc in wiki_cats_lower):
                    w += 2
                scores[cat] += w
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else config.DEFAULT_CATEGORY

# ── 4. RELEVANCE (uses config.py) ─────────────────────────────────────────────

SPECIFIC_CATS = set(config.CATEGORY_LIST) - {config.DEFAULT_CATEGORY}
_NON_BIO_KW_RE = re.compile('|'.join(config.NON_BIO_KW_PATTERNS), re.IGNORECASE) if config.NON_BIO_KW_PATTERNS else None

def _is_off_subject(keyword, all_page_cats, cleaned_text):
    content_check = cleaned_text[:400].lower()
    cats_check = ' '.join(all_page_cats).lower()

    if _NON_BIO_KW_RE and _NON_BIO_KW_RE.search(keyword):
        return True, 'Off-subject keyword pattern'
    for signal in config.NON_BIO_CONTENT_SIGNALS:
        if signal in content_check:
            return True, f'Off-subject content signal: "{signal}"'
    for signal in config.NON_BIO_CAT_SIGNALS:
        if signal in cats_check:
            return True, f'Off-subject Wikipedia category: "{signal}"'
    return False, ''

def is_relevant(keyword, categories, all_page_cats, cleaned_text, assigned_category, raw_text=''):
    kw_lower = keyword.lower()
    raw_lower = cleaned_text[:400].lower()
    all_cats_lower = ' '.join(categories + all_page_cats).lower()

    if kw_lower.startswith('template:'):
        return False, 'Wikipedia template/navigation page, not an article'
    for phrase in config.IRRELEVANT_SIGNALS:
        if phrase in raw_lower:
            return False, 'Disambiguation or set-index page'
    raw_text_lower = raw_text.lower()
    for tmpl in ['{{disambig', '{{disambiguation', '{{wiktionary', '{{toc right', '{{tocright', 'toc right}}', '{{set index']:
        if tmpl in raw_text_lower:
            return False, 'Disambiguation or set-index page'
    if kw_lower.startswith('category:') and len(cleaned_text) < 80:
        return False, 'Wikipedia category page with insufficient content'

    off_subject, reason = _is_off_subject(keyword, all_page_cats, cleaned_text)
    if off_subject:
        return False, reason

    if assigned_category in SPECIFIC_CATS:
        return True, ''

    probe = kw_lower + ' ' + all_cats_lower + ' ' + raw_lower
    for kw in config.BIO_KEYWORDS:
        if kw in probe:
            return True, ''
    return False, f'Not clearly related to {config.DOMAIN_NAME}'

# ── 5. ARTIFACT CHECK ─────────────────────────────────────────────────────────

ARTIFACT_RE = re.compile(r'\{\{|\}\}|\[\[|\]\]|<ref|thumb\||\d+px|&[a-z]{2,8};|&#\d+;', re.IGNORECASE)

def has_artifacts(text):
    return bool(ARTIFACT_RE.search(text))

# ── 6. PER-ENTRY PROCESSING ───────────────────────────────────────────────────

def process_entry(entry: dict) -> dict:
    keyword       = entry.get('keyword', '').strip()
    raw_text      = entry.get('definition_short', '') or entry.get('definition_raw', '')
    categories    = entry.get('categories', [])
    all_page_cats = entry.get('all_page_categories', categories)
    source_url    = entry.get('source_url', '')
    images        = entry.get('images', [])

    cleaned  = clean_wikipedia_markup(raw_text)
    cleaned  = ensure_two_paragraphs(cleaned)
    category = assign_category(keyword, categories, all_page_cats, cleaned)
    relevant, reason = is_relevant(keyword, categories, all_page_cats, cleaned, category, raw_text)
    short    = make_definition_short(keyword, cleaned) if relevant else ''

    return {
        "keyword":             keyword,
        "definition_short":    short,
        "definition_raw":      cleaned if relevant else '',
        "source_url":          source_url,
        "images":              images,
        "categories":          categories,
        "all_page_categories": all_page_cats,
        "category":            category,
        "relevant":            relevant,
        "_irrelevant_reason":  reason if not relevant else None,
    }

# ── 7. CLI ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Stage 2: rule-based Wikipedia text cleaning")
    p.add_argument('--input',  default='../01_wiki_extract/subwiki.jsonl')
    p.add_argument('--output', default='keywords_clean.jsonl')
    p.add_argument('--limit',  type=int, default=0, help='Only process the first N entries (for smoke testing)')
    args = p.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    total = relevant_count = irrelevant_count = 0
    with open(input_path, encoding='utf-8') as fin, open(args.output, 'w', encoding='utf-8') as fout:
        for i, line in enumerate(fin):
            if args.limit and i >= args.limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            out = process_entry(entry)
            reason = out.pop('_irrelevant_reason', None)
            fout.write(json.dumps(out, ensure_ascii=False) + '\n')

            total += 1
            if out['relevant']:
                relevant_count += 1
            else:
                irrelevant_count += 1
                if total <= 20:
                    print(f"  [irrelevant] {out['keyword']!r}: {reason}")

    print(f"\nProcessed {total} entries -> {args.output}")
    print(f"  relevant:   {relevant_count}")
    print(f"  irrelevant: {irrelevant_count}")


if __name__ == '__main__':
    main()
