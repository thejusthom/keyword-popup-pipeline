#!/usr/bin/env python3
"""
Process oncology_subwiki.jsonl → keywords_clean.jsonl  (v2)
"""
import json, re, sys
from pathlib import Path

# ── 1. CLEANER ──────────────────────────────────────────────

def remove_html_comments(text):
    # Literal <!-- --> AND html-encoded &lt;!-- --&gt;
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
    # Standard [[File:...]]
    for _ in range(5):
        prev = text
        text = re.sub(
            r'\[\[(?:File|Image|Media):[^\[\]]*(?:\[\[[^\[\]]*\]\][^\[\]]*)*\]\]',
            '', text, flags=re.IGNORECASE)
        if text == prev:
            break
    # Catch any remaining [[File: that wasn't closed
    text = re.sub(r'\[\[(?:File|Image|Media):[^\]]*(?:\](?!\]))?', '', text, flags=re.IGNORECASE)
    return text

def convert_wikilinks(text):
    text = re.sub(r'\[\[(?:[^\[\]|]*\|)([^\[\]|]*)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\[\]|]*)\]\]', r'\1', text)
    # Strip any remaining [[ or ]] that didn't get matched (malformed)
    text = text.replace('[[', '').replace(']]', '')
    return text

def decode_html_entities(text):
    reps = {
        '&amp;':'&','&lt;':'<','&gt;':'>','&nbsp;':' ','&quot;':'"',
        '&#160;':' ','&mdash;':'—','&ndash;':'–','&hellip;':'…','&times;':'×',
        '&alpha;':'α','&beta;':'β','&gamma;':'γ','&delta;':'δ','&sigma;':'σ',
        '&mu;':'μ','&#39;':"'",'&apos;':"'",'&lsquo;':"'",'&rsquo;':"'",
        '&ldquo;':'"','&rdquo;':'"','&minus;':'−','&plusmn;':'±','&deg;':'°',
        '&sup2;':'²','&sup3;':'³','&prime;':'′','&isin;':'∈','&rarr;':'→',
        '&asymp;':'≈','&le;':'≤','&ge;':'≥','&ne;':'≠','&infin;':'∞',
        '&kappa;':'κ','&lambda;':'λ','&omega;':'ω','&pi;':'π','&rho;':'ρ',
        '&tau;':'τ','&upsilon;':'υ','&phi;':'φ','&chi;':'χ','&psi;':'ψ',
        '&Sigma;':'Σ','&Delta;':'Δ','&Omega;':'Ω','&theta;':'θ','&eta;':'η',
        '&epsilon;':'ε','&zeta;':'ζ','&shy;':'',  # soft hyphen → remove
    }
    for ent, ch in reps.items():
        text = text.replace(ent, ch)
    try:
        text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    except Exception:
        pass
    # Remove any remaining & entities that didn't decode
    text = re.sub(r'&[a-zA-Z]{2,10};', '', text)
    return text

def remove_refs(text):
    # Now that entities are decoded, <ref> tags are actual < >
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    text = re.sub(r'</ref>', '', text)
    text = re.sub(r'<ref[^>]*>', '', text)
    return text

def remove_html_tags(text):
    text = re.sub(r'<[^>]{0,60}>', '', text)
    return text

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
        elif len(stripped) > 10 and not re.fullmatch(r'[^\w\s-￿]*', stripped):
            clean_lines.append(stripped)
    text = '\n'.join(clean_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def clean_wikipedia_markup(text: str) -> str:
    if not text:
        return ''
    text = remove_html_comments(text)   # must be before entity decoding
    text = remove_templates(text)
    text = decode_html_entities(text)   # BEFORE refs — so &lt;ref&gt; → <ref>
    text = remove_refs(text)            # now catches all ref forms
    text = remove_file_links(text)
    text = remove_html_tags(text)
    text = remove_category_links(text)
    text = remove_external_links(text)
    text = convert_wikilinks(text)      # also strips remaining [[ ]]
    text = remove_image_artifacts(text)
    text = remove_wiki_formatting(text)
    text = clean_whitespace(text)
    return text

# ── 2. PARAGRAPH UTILS ──────────────────────────────────────

def split_into_sentences(text):
    text = re.sub(r'\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|vs|etc|Fig|i\.e|e\.g|cf|approx|No|Vol|pp|ed|et al)\.\s', r'\1@@@ ', text)
    text = re.sub(r'\b([A-Z]{1,2})\.\s', r'\1@@@ ', text)
    sents = re.split(r'(?<=[.!?])\s+', text)
    sents = [s.replace('@@@', '.').strip() for s in sents if len(s.strip()) > 5]
    return sents

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
    selected = []
    char_count = 0
    for s in sents[start_idx:start_idx + 4]:
        if char_count + len(s) > 500 and len(selected) >= 2:
            break
        selected.append(s)
        char_count += len(s)
        if len(selected) >= 3:
            break
    return ' '.join(selected)

# ── 3. CATEGORY ──────────────────────────────────────────────

PRIORITY_ORDER = [
    'Oncology','Nanomedicine','Nanotechnology','Biotechnology',
    'Immunology','Genetics','Molecular Biology','Cell Biology',
    'Pharmacology','Biochemistry','Diagnostics','Anatomy','Chemistry',
    'Biomedical Science'
]

WIKI_CAT_MAP = {
    'oncology':'Oncology','cancer':'Oncology','tumor':'Oncology','leukemia':'Oncology',
    'lymphoma':'Oncology','melanoma':'Oncology','carcinoma':'Oncology',
    'nanomedicine':'Nanomedicine','nanotechnology':'Nanotechnology','nanoparticle':'Nanotechnology',
    'biotechnology':'Biotechnology','immunology':'Immunology','genetics':'Genetics',
    'genomics':'Genetics','epigenetics':'Genetics','molecular biology':'Molecular Biology',
    'cell biology':'Cell Biology','cell signaling':'Cell Biology',
    'signal transduction':'Cell Biology','organelles':'Cell Biology','apoptosis':'Cell Biology',
    'biochemistry':'Biochemistry','metabolism':'Biochemistry','enzymes':'Biochemistry',
    'protein':'Biochemistry','pharmacology':'Pharmacology','drug discovery':'Pharmacology',
    'chemotherapy':'Pharmacology','medical imaging':'Diagnostics','diagnostic':'Diagnostics',
    'pathology':'Diagnostics','anatomy':'Anatomy','chemistry':'Chemistry',
    'crystallography':'Chemistry','mass spectrometry':'Chemistry',
    'bioinformatics':'Molecular Biology','gene expression':'Molecular Biology',
    'microrna':'Molecular Biology','proteomics':'Biochemistry',
    'transcription':'Molecular Biology','virology':'Biomedical Science',
    'bacteriology':'Biomedical Science','microbiology':'Biomedical Science',
    'physiology':'Biomedical Science','biophysics':'Biomedical Science',
    'toxicology':'Pharmacology','systems biology':'Molecular Biology',
    'membrane biology':'Cell Biology',
}

CATEGORY_KEYWORDS = {
    'Oncology':['cancer','tumor','tumour','carcinoma','oncogen','metastas','leukemia',
        'leukaemia','lymphoma','sarcoma','melanoma','glioma','blastoma','chemotherap',
        'antineoplastic','oncolog','malignant','malignancy','neoplasm','neoplastic',
        'carcinogenesis','tumorigenesis','angiogenesis','myeloma','mesothelioma'],
    'Nanomedicine':['nanomedicine','nanoparticle-based','liposomal drug','nanocarrier',
        'theranostic','targeted drug delivery','nanotherap','nanoplatform',
        'polymer nanoparticle','gold nanoparticle','magnetic nanoparticle'],
    'Nanotechnology':['nanotechnolog','nanotube','nanowire','nanostructure','nanomaterial',
        'nanoscale','nanocomposite','fullerene','graphene','nanofabrication',
        'self-assembl','atomic force microscop','scanning tunneling','nanorod',
        'nanofiber','quantum dot','nanoparticle','dendrim'],
    'Biotechnology':['biotechnolog','recombinant dna','recombinant protein','fermentation',
        'bioreactor','crispr','gene editing','gene therapy','cloning','transgenic',
        'biomanufacturing','monoclonal antibody','biosimilar','bioprocess',
        'polymerase chain reaction','genome editing','synthetic biology',
        'directed evolution','phage display','lentiviral','adeno-associated virus'],
    'Immunology':['antibod','immune response','t cell','b cell','antigen','cytokine',
        'lymphocyte','immunolog','inflammation','innate immun','adaptive immun',
        'complement system','interferon','interleukin','natural killer','macrophage',
        'dendritic cell','immunotherap','checkpoint inhibitor','pd-l1','pd-1','ctla-4',
        'car-t','chimeric antigen','autoimmun','mhc','major histocompatibility',
        'toll-like receptor'],
    'Genetics':['gene ','chromosome','mutation','inheritance','allele','snp',
        'genotype','phenotype','haplotype','genomic','epigenetic','methylation',
        'histone','brca','hereditary','genetic disorder','locus','polymorphism',
        'variant','germline','somatic mutation','copy number','chromosomal','karyotype'],
    'Molecular Biology':['dna replication','rna polymerase','transcription factor',
        'translation','gene expression','mrna','protein synthesis','rna splicing',
        'nucleic acid','promoter region','enhancer','silencer','intron','exon',
        'codon','ribosom','rna interference','sirna','microrna','mirna',
        'non-coding rna','lncrna','chromatin','nucleosome','western blot','pcr',
        'microarray','rna-seq','chip-seq','next-generation sequencing','cdna'],
    'Cell Biology':['cell cycle','cell division','mitosis','meiosis','apoptosis',
        'autophagy','cytoskeleton','mitochondria','cell membrane','endoplasmic reticulum',
        'golgi','lysosom','vesicle','vacuole','cell proliferation','differentiation',
        'stem cell','cell signaling','signal transduction','receptor tyrosine kinase',
        'organelle','cytoplasm','nucleus','nucleolus','centrosome','cell death',
        'necrosis','senescence','cell migration','invasion'],
    'Pharmacology':['drug ','medication','pharmacokinetic','pharmacodynamic','pharmacolog',
        'toxicity','inhibitor','agonist','antagonist','bioavailability','half-life',
        'therapeutic','clinical trial','doxorubicin','cisplatin','paclitaxel','taxol',
        'imatinib','tamoxifen','bevacizumab','trastuzumab','dose','dosage',
        'drug resistance','ic50','ec50','ld50'],
    'Biochemistry':['enzyme','metabol','amino acid','glucose','atp ','nadh','nadph',
        'lipid','carbohydrate','glycolysis','citric acid cycle','krebs cycle',
        'oxidative phosphorylation','cofactor','substrate','kinetics','redox',
        'oxidation','reduction','biochem','catabolism','anabolism','protein folding',
        'post-translational','glycosylation','phosphorylation','ubiquitin',
        'proteasome','fatty acid','cholesterol','steroid','nucleotide','atp synthase',
        'electron transport','coenzyme'],
    'Diagnostics':['diagnos','medical imaging','biomarker','assay','biopsy',
        'screening','mri ','ct scan','pet scan','ultrasound','x-ray','radiolog',
        'patholog','prognosis','staging','detection method','immunohistochemistry',
        'flow cytometry','elisa','blood test','tumor marker','liquid biopsy',
        'circulating tumor','positron emission','fluorescence imaging'],
    'Anatomy':['anatomy','anatomical','organ ','skeletal','skeleton','nerve ',
        'muscle ','bone ','lymph node','blood vessel','artery','vein',
        'histolog','morpholog','tissue structure','epithelium','endothelium',
        'stroma','vasculature','lymphatic'],
    'Chemistry':['chemical compound','organic chemistry','inorganic chemistry',
        'polymer','monomer','catalyst','synthesis of','chemical reaction',
        'chemical structure','covalent bond','ionic bond','molecule',
        'spectroscop','chromatograph','mass spectrometry','nmr ',
        'crystallography','isotope','radioactive'],
    'Biomedical Science':['biomedical','clinical','medical research','health','disease',
        'patient','treatment','therapy','hospital','physiology','pathophysiology',
        'in vivo','in vitro','animal model'],
}

def assign_category(keyword, categories, all_page_cats, cleaned_text):
    kw_lower = keyword.lower()
    wiki_cats_lower = [c.lower() for c in (categories + all_page_cats)]
    for wc in wiki_cats_lower:
        for key, cat in WIKI_CAT_MAP.items():
            if key in wc:
                return cat
    probe = (kw_lower + ' ' + ' '.join(wiki_cats_lower) + ' ' + cleaned_text[:600].lower())
    scores = {cat: 0 for cat in PRIORITY_ORDER}
    for cat in PRIORITY_ORDER:
        for pattern in CATEGORY_KEYWORDS[cat]:
            if pattern in probe:
                w = 1
                if pattern in kw_lower: w += 4
                if any(pattern in wc for wc in wiki_cats_lower): w += 2
                scores[cat] += w
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else 'Biomedical Science'

# ── 4. RELEVANCE ─────────────────────────────────────────────

SPECIFIC_CATS = set(PRIORITY_ORDER) - {'Biomedical Science'}
BIO_KEYWORDS = ['biolog','medical','medicine','cell','protein','gene','dna','rna',
    'clinical','organism','bacteria','virus','disease','treatment','biochem',
    'molecular','genetic','immune','receptor','enzyme','metabol','tissue','organ',
    'patholog','physiolog','tumor','cancer','drug','therapeutic','diagnostic',
    'sequencing','biomarker','assay','phys','chem','nano','protein','peptide',
    'genomic','proteomic','transcriptomic','epigenet','biophysic']
IRRELEVANT_SIGNALS = ['may refer to:','refers to:','set index article','disambiguation']

# Content signals in the first 400 chars that definitively mean non-biomedical,
# regardless of what category the classifier assigned.
NON_BIO_CONTENT_SIGNALS = [
    # Language / linguistics translation
    'is the translation of language', 'source-language text', 'target-language',
    'source language text', 'is an audiovisual translation', 'voice-over',
    'is the study of translation', 'translation of legal', 'legal settings',
    'back-and-forth translation', 'round-trip translation',
    'indeterminacy of translation',
    # Plays / literature / arts
    'is a play by', 'is a three-act play', 'is a two-act play', 'wrote the play',
    'is a novel by', 'is a film by', 'is a television', 'is an opera',
    'is a song by', 'is an album by',
    # Real estate / urban development
    'mixed-use development', 'square feet of', 'residential tower', 'office space',
    'development complex', 'real estate',
    # Geography
    'is a city in', 'is a town in', 'is a village in', 'is a municipality',
    'is a county in', 'is located in the',
    # Biography
    'was an american', 'was a british', 'was an english', 'was a french',
    'was a german', 'was a writer', 'was a poet', 'was a composer',
    'was a philosopher', 'was an actor', 'was a director', 'was a politician',
    'was a novelist', 'was a playwright', 'was a filmmaker',
    # Sports / military
    'is a professional football', 'is a football club', 'is a cricket',
    'is a military', 'is a missile', 'is a warship', 'is a fighter aircraft',
    # Space / astronomy
    'international space station', 'outer space', 'space shuttle',
    'astronomical', 'astrophysic',
]

# Wikipedia page-category strings that flag non-biomedical content.
NON_BIO_CAT_SIGNALS = [
    'machine translation', 'translation studies', 'legal communication',
    'language games', 'philosophy of language', 'evaluation of machine translation',
    'plays by ', 'novels by ', 'films by ', 'albums by ', 'songs by ',
    'television series', '1980 plays', '1990 plays', '2000 plays',
    'real estate', 'residential areas', 'census-designated places',
    'populated places',
]

# Keyword suffix/form patterns that are almost never biomedical
NON_BIO_KW_PATTERNS = [
    r'\(play\)$', r'\(film\)$', r'\(novel\)$', r'\(album\)$',
    r'\(song\)$', r'\(TV series\)$', r'\(opera\)$',
    r'^legal translation', r'^certified translation', r'^voice.over translation',
    r'^round.trip translation', r'^literary translation', r'^bible translation',
    r'^machine translation',
]
_NON_BIO_KW_RE = re.compile('|'.join(NON_BIO_KW_PATTERNS), re.IGNORECASE)


def _is_non_biomedical(keyword, all_page_cats, cleaned_text):
    """Return (True, reason) if clear non-biomedical signals are found."""
    kw_lower = keyword.lower()
    content_check = cleaned_text[:400].lower()
    cats_check    = ' '.join(all_page_cats).lower()

    if _NON_BIO_KW_RE.search(keyword):
        return True, 'Non-biomedical keyword pattern (play/film/novel/language translation)'

    for signal in NON_BIO_CONTENT_SIGNALS:
        if signal in content_check:
            return True, f'Non-biomedical content signal: "{signal}"'

    for signal in NON_BIO_CAT_SIGNALS:
        if signal in cats_check:
            return True, f'Non-biomedical Wikipedia category: "{signal}"'

    return False, ''


def is_relevant(keyword, categories, all_page_cats, cleaned_text, assigned_category, raw_text=''):
    kw_lower = keyword.lower()
    raw_lower = cleaned_text[:400].lower()
    all_cats_lower = ' '.join(categories + all_page_cats).lower()
    # Template pages are navigation boxes, not educational content
    if kw_lower.startswith('template:'):
        return False, 'Wikipedia template/navigation page, not an educational article'
    for phrase in IRRELEVANT_SIGNALS:
        if phrase in raw_lower:
            return False, 'Disambiguation or set-index page'
    # Check raw text for disambiguation templates before cleaning
    raw_text_lower = raw_text.lower()
    for tmpl in ['{{disambig', '{{disambiguation', '{{wiktionary', '{{toc right', '{{tocright', 'toc right}}', '{{set index']:
        if tmpl in raw_text_lower:
            return False, 'Disambiguation or set-index page'
    if kw_lower.startswith('category:') and len(cleaned_text) < 80:
        return False, 'Wikipedia category page with insufficient content'
    # Content-based non-biomedical check — runs BEFORE the category shortcut
    # so that misclassified entries (e.g. language translation assigned to
    # Molecular Biology because of a category name collision) are still caught.
    non_bio, reason = _is_non_biomedical(keyword, all_page_cats, cleaned_text)
    if non_bio:
        return False, reason
    if assigned_category in SPECIFIC_CATS:
        return True, ''
    probe = kw_lower + ' ' + all_cats_lower + ' ' + raw_lower
    for kw in BIO_KEYWORDS:
        if kw in probe:
            return True, ''
    return False, 'Not related to cancer, nanomedicine, nanotechnology, biotechnology, or foundational biology/chemistry'

# ── 5. ARTIFACT CHECK ────────────────────────────────────────

ARTIFACT_RE = re.compile(r'\{\{|\}\}|\[\[|\]\]|<ref|thumb\||\d+px|&[a-z]{2,8};|&#\d+;', re.IGNORECASE)

def has_artifacts(text):
    return bool(ARTIFACT_RE.search(text))

# ── 6. MAIN ──────────────────────────────────────────────────

def process_entry(entry):
    keyword       = entry.get('keyword', '').strip()
    r