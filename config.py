"""
config.py — THE ONLY FILE YOU NEED TO EDIT TO POINT THIS PIPELINE AT A NEW TEXTBOOK.

Every script in this pipeline (01 through 07) imports values from here instead of
hardcoding them. To adapt the whole pipeline to a new subject, change the values
below — you should not need to touch any of the numbered stage folders.

The values below are filled in with a real, working example (an oncology /
cancer-biology textbook) so you can see the shape of a complete config. Replace
them with your own subject's values.

Exception — Stage 3 (`03_wiki_rewrite/rewrite_wiki.py`) also has a block of
worked examples ("few-shot examples") hardcoded directly in that file, not here.
Those are genuinely subject-specific (they're example input/output pairs the LLM
learns from) and can't be reduced to config values — see the comment at the top
of that file for what to replace them with.
"""

# ─────────────────────────────────────────────────────────────────────────────
# 1. SUBJECT IDENTITY — used in LLM prompts throughout stages 3 and 5
# ─────────────────────────────────────────────────────────────────────────────

DOMAIN_NAME = "oncology"                     # e.g. "constitutional law", "macroeconomics"
DOMAIN_DESCRIPTION = (
    "Cancer, Nanomedicine, Nanotechnology, and Biotechnology"
)  # one line describing the textbook's scope, used in stage 3's system prompt

TEXTBOOK_PLATFORM_NAME = "Medhavi"           # used in stage 3's system prompt; change or remove


# ─────────────────────────────────────────────────────────────────────────────
# 2. PATHS — shared across every stage
# ─────────────────────────────────────────────────────────────────────────────

DOCS_ROOT = "../docs"          # your textbook's MDX/Markdown source directory
                                 # (relative to wherever you run the stage scripts from —
                                 #  or set an absolute path)


# ─────────────────────────────────────────────────────────────────────────────
# 3. CATEGORIES — the fixed set of labels every keyword gets assigned to
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_LIST = [
    "Oncology", "Nanomedicine", "Nanotechnology", "Biotechnology",
    "Molecular Biology", "Cell Biology", "Biochemistry", "Genetics",
    "Immunology", "Pharmacology", "Anatomy", "Chemistry",
    "Diagnostics", "Biomedical Science",
]

DEFAULT_CATEGORY = "Biomedical Science"      # fallback when nothing else matches

# Maps a lowercase substring found in a Wikipedia category name to one of the
# labels in CATEGORY_LIST above. Used by Stage 2 for fast, free category
# assignment before falling back to keyword scoring.
WIKI_CAT_MAP = {
    'oncology': 'Oncology', 'cancer': 'Oncology', 'tumor': 'Oncology',
    'leukemia': 'Oncology', 'lymphoma': 'Oncology', 'melanoma': 'Oncology',
    'carcinoma': 'Oncology',
    'nanomedicine': 'Nanomedicine',
    'nanotechnology': 'Nanotechnology', 'nanoparticle': 'Nanotechnology',
    'biotechnology': 'Biotechnology',
    'immunology': 'Immunology', 'genetics': 'Genetics', 'genomics': 'Genetics',
    'epigenetics': 'Genetics', 'molecular biology': 'Molecular Biology',
    'cell biology': 'Cell Biology', 'cell signaling': 'Cell Biology',
    'signal transduction': 'Cell Biology', 'organelles': 'Cell Biology',
    'apoptosis': 'Cell Biology', 'biochemistry': 'Biochemistry',
    'metabolism': 'Biochemistry', 'enzymes': 'Biochemistry',
    'protein': 'Biochemistry', 'pharmacology': 'Pharmacology',
    'drug discovery': 'Pharmacology', 'chemotherapy': 'Pharmacology',
    'medical imaging': 'Diagnostics', 'diagnostic': 'Diagnostics',
    'pathology': 'Diagnostics', 'anatomy': 'Anatomy', 'chemistry': 'Chemistry',
    'crystallography': 'Chemistry', 'mass spectrometry': 'Chemistry',
    'bioinformatics': 'Molecular Biology', 'gene expression': 'Molecular Biology',
    'microrna': 'Molecular Biology', 'proteomics': 'Biochemistry',
    'transcription': 'Molecular Biology', 'virology': 'Biomedical Science',
    'bacteriology': 'Biomedical Science', 'microbiology': 'Biomedical Science',
    'physiology': 'Biomedical Science', 'biophysics': 'Biomedical Science',
    'toxicology': 'Pharmacology', 'systems biology': 'Molecular Biology',
    'membrane biology': 'Cell Biology',
}

# Fallback scoring: keyword patterns searched in the keyword name + Wikipedia
# categories + first 600 chars of body text when WIKI_CAT_MAP finds no match.
CATEGORY_KEYWORDS = {
    'Oncology': ['cancer', 'tumor', 'tumour', 'carcinoma', 'oncogen', 'metastas',
        'leukemia', 'leukaemia', 'lymphoma', 'sarcoma', 'melanoma', 'glioma',
        'blastoma', 'chemotherap', 'antineoplastic', 'oncolog', 'malignant',
        'malignancy', 'neoplasm', 'neoplastic', 'carcinogenesis', 'tumorigenesis',
        'angiogenesis', 'myeloma', 'mesothelioma'],
    'Nanomedicine': ['nanomedicine', 'nanoparticle-based', 'liposomal drug',
        'nanocarrier', 'theranostic', 'targeted drug delivery', 'nanotherap',
        'nanoplatform', 'polymer nanoparticle', 'gold nanoparticle',
        'magnetic nanoparticle'],
    'Nanotechnology': ['nanotechnolog', 'nanotube', 'nanowire', 'nanostructure',
        'nanomaterial', 'nanoscale', 'nanocomposite', 'fullerene', 'graphene',
        'nanofabrication', 'self-assembl', 'atomic force microscop',
        'scanning tunneling', 'nanorod', 'nanofiber', 'quantum dot',
        'nanoparticle', 'dendrim'],
    'Biotechnology': ['biotechnolog', 'recombinant dna', 'recombinant protein',
        'fermentation', 'bioreactor', 'crispr', 'gene editing', 'gene therapy',
        'cloning', 'transgenic', 'biomanufacturing', 'monoclonal antibody',
        'biosimilar', 'bioprocess', 'polymerase chain reaction',
        'genome editing', 'synthetic biology', 'directed evolution',
        'phage display', 'lentiviral', 'adeno-associated virus'],
    'Immunology': ['antibod', 'immune response', 't cell', 'b cell', 'antigen',
        'cytokine', 'lymphocyte', 'immunolog', 'inflammation', 'innate immun',
        'adaptive immun', 'complement system', 'interferon', 'interleukin',
        'natural killer', 'macrophage', 'dendritic cell', 'immunotherap',
        'checkpoint inhibitor', 'pd-l1', 'pd-1', 'ctla-4', 'car-t',
        'chimeric antigen', 'autoimmun', 'mhc', 'major histocompatibility',
        'toll-like receptor'],
    'Genetics': ['gene ', 'chromosome', 'mutation', 'inheritance', 'allele',
        'snp', 'genotype', 'phenotype', 'haplotype', 'genomic', 'epigenetic',
        'methylation', 'histone', 'brca', 'hereditary', 'genetic disorder',
        'locus', 'polymorphism', 'variant', 'germline', 'somatic mutation',
        'copy number', 'chromosomal', 'karyotype'],
    'Molecular Biology': ['dna replication', 'rna polymerase',
        'transcription factor', 'translation', 'gene expression', 'mrna',
        'protein synthesis', 'rna splicing', 'nucleic acid', 'promoter region',
        'enhancer', 'silencer', 'intron', 'exon', 'codon', 'ribosom',
        'rna interference', 'sirna', 'microrna', 'mirna', 'non-coding rna',
        'lncrna', 'chromatin', 'nucleosome', 'western blot', 'pcr',
        'microarray', 'rna-seq', 'chip-seq', 'next-generation sequencing',
        'cdna'],
    'Cell Biology': ['cell cycle', 'cell division', 'mitosis', 'meiosis',
        'apoptosis', 'autophagy', 'cytoskeleton', 'mitochondria',
        'cell membrane', 'endoplasmic reticulum', 'golgi', 'lysosom',
        'vesicle', 'vacuole', 'cell proliferation', 'differentiation',
        'stem cell', 'cell signaling', 'signal transduction',
        'receptor tyrosine kinase', 'organelle', 'cytoplasm', 'nucleus',
        'nucleolus', 'centrosome', 'cell death', 'necrosis', 'senescence',
        'cell migration', 'invasion'],
    'Pharmacology': ['drug ', 'medication', 'pharmacokinetic',
        'pharmacodynamic', 'pharmacolog', 'toxicity', 'inhibitor', 'agonist',
        'antagonist', 'bioavailability', 'half-life', 'therapeutic',
        'clinical trial', 'doxorubicin', 'cisplatin', 'paclitaxel', 'taxol',
        'imatinib', 'tamoxifen', 'bevacizumab', 'trastuzumab', 'dose',
        'dosage', 'drug resistance', 'ic50', 'ec50', 'ld50'],
    'Biochemistry': ['enzyme', 'metabol', 'amino acid', 'glucose', 'atp ',
        'nadh', 'nadph', 'lipid', 'carbohydrate', 'glycolysis',
        'citric acid cycle', 'krebs cycle', 'oxidative phosphorylation',
        'cofactor', 'substrate', 'kinetics', 'redox', 'oxidation',
        'reduction', 'biochem', 'catabolism', 'anabolism', 'protein folding',
        'post-translational', 'glycosylation', 'phosphorylation',
        'ubiquitin', 'proteasome', 'fatty acid', 'cholesterol', 'steroid',
        'nucleotide', 'atp synthase', 'electron transport', 'coenzyme'],
    'Diagnostics': ['diagnos', 'medical imaging', 'biomarker', 'assay',
        'biopsy', 'screening', 'mri ', 'ct scan', 'pet scan', 'ultrasound',
        'x-ray', 'radiolog', 'patholog', 'prognosis', 'staging',
        'detection method', 'immunohistochemistry', 'flow cytometry',
        'elisa', 'blood test', 'tumor marker', 'liquid biopsy',
        'circulating tumor', 'positron emission', 'fluorescence imaging'],
    'Anatomy': ['anatomy', 'anatomical', 'organ ', 'skeletal', 'skeleton',
        'nerve ', 'muscle ', 'bone ', 'lymph node', 'blood vessel', 'artery',
        'vein', 'histolog', 'morpholog', 'tissue structure', 'epithelium',
        'endothelium', 'stroma', 'vasculature', 'lymphatic'],
    'Chemistry': ['chemical compound', 'organic chemistry',
        'inorganic chemistry', 'polymer', 'monomer', 'catalyst',
        'synthesis of', 'chemical reaction', 'chemical structure',
        'covalent bond', 'ionic bond', 'molecule', 'spectroscop',
        'chromatograph', 'mass spectrometry', 'nmr ', 'crystallography',
        'isotope', 'radioactive'],
    'Biomedical Science': ['biomedical', 'clinical', 'medical research',
        'health', 'disease', 'patient', 'treatment', 'therapy', 'hospital',
        'physiology', 'pathophysiology', 'in vivo', 'in vitro',
        'animal model'],
}


# ─────────────────────────────────────────────────────────────────────────────
# 4. RELEVANCE FILTER — Stage 2's free, rule-based irrelevant-content catcher
# ─────────────────────────────────────────────────────────────────────────────

# Substrings found in the first ~400 chars of body text that mean "wrong
# subject, mark relevant=false" regardless of what category was assigned.
NON_BIO_CONTENT_SIGNALS = [
    'is the translation of language', 'source-language text',
    'target-language', 'is an audiovisual translation', 'voice-over',
    'is the study of translation', 'is a city in', 'is a town in',
    'is a village in', 'is a municipality', 'is a county in',
    'is located in the', 'was an american', 'was a british',
    'was an english', 'was a french', 'was a german', 'was a writer',
    'was a poet', 'was a composer', 'was a philosopher', 'was an actor',
    'was a director', 'was a politician', 'was a novelist',
    'was a playwright', 'was a filmmaker', 'is a professional football',
    'is a football club', 'is a cricket', 'is a military', 'is a missile',
    'is a warship', 'is a fighter aircraft', 'international space station',
    'outer space', 'space shuttle', 'astronomical', 'astrophysic',
]

# Wikipedia page-category strings that flag off-subject content.
NON_BIO_CAT_SIGNALS = [
    'machine translation', 'translation studies', 'legal communication',
    'language games', 'philosophy of language',
    'evaluation of machine translation', 'plays by ', 'novels by ',
    'films by ', 'albums by ', 'songs by ', 'television series',
    '1980 plays', '1990 plays', '2000 plays', 'real estate',
    'residential areas', 'census-designated places', 'populated places',
]

# Regex-matched keyword suffix/prefix patterns that are almost never on-subject
NON_BIO_KW_PATTERNS = [
    r'\(play\)$', r'\(film\)$', r'\(novel\)$', r'\(album\)$', r'\(song\)$',
    r'\(TV series\)$', r'\(opera\)$', r'^legal translation',
    r'^certified translation', r'^voice.over translation',
    r'^round.trip translation', r'^literary translation',
    r'^bible translation', r'^machine translation',
]

# Free-text substrings that always signal a disambiguation / set-index page
IRRELEVANT_SIGNALS = [
    'may refer to:', 'refers to:', 'set index article', 'disambiguation',
]

# Generic "is this on-subject at all" keyword probe, used as a last resort
# when nothing else has made a relevance decision.
BIO_KEYWORDS = ['biolog', 'medical', 'medicine', 'cell', 'protein', 'gene',
    'dna', 'rna', 'clinical', 'organism', 'bacteria', 'virus', 'disease',
    'treatment', 'biochem', 'molecular', 'genetic', 'immune', 'receptor',
    'enzyme', 'metabol', 'tissue', 'organ', 'patholog', 'physiolog', 'tumor',
    'cancer', 'drug', 'therapeutic', 'diagnostic', 'sequencing', 'biomarker',
    'assay', 'phys', 'chem', 'nano', 'peptide', 'genomic', 'proteomic',
    'transcriptomic', 'epigenet', 'biophysic']


# ─────────────────────────────────────────────────────────────────────────────
# 5. ABBREVIATION SCANNING (Stage 4) — noise filter + category inference
# ─────────────────────────────────────────────────────────────────────────────

# Abbreviations to always discard as noise, regardless of subject. Roman
# numerals, markup tokens, and common English words caught by the regex are
# universal; extend this set with your own subject's false positives
# (cell line names, product codes, staging notation, etc. for medicine;
# case citation formats for law; ticker-symbol collisions for finance).
ABBREV_SKIP = {
    "MDX", "JSX", "TSX", "HTML", "CSS", "JSON", "API", "URL", "HTTP", "HTTPS",
    "NULL", "TRUE", "FALSE", "EOF", "TODO", "FIXME",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII", "XIV",
    "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "AND", "NOT", "MIN", "OFF", "USE", "TERM", "NOTES", "TEXT", "BEING", "LED",
    "TB", "ML", "EU", "UK", "USA", "HM", "NT", "GE", "OK", "ST",
    "CC", "SF", "HM",
    # ── example subject-specific noise (oncology) — replace for your subject ──
    "IIA", "IIB", "IIC", "IIIA", "IIIB", "IIIC",
    "HCT116", "SW480", "MCF7", "NIH3T3",
    "NP-40", "PVDF",
}

# Keyword-pattern → category inference for abbreviations the scanner finds
# that aren't in a hand-curated definitions list. Checked in order; first
# match wins. Extend/replace per subject.
ABBREV_CATEGORY_RULES = [
    ("Oncology", ["CANCER", "CARCI", "TUMOR", "SARC", "LYMPH", "LEUK", "MELA",
                  "NSCLC", "SCLC", "GBM", "HCC", "CRC", "AML", "CLL", "MCL",
                  "PDAC", "TNBC", "RCC", "BCC", "SCC", "HNSCC"]),
    ("Immunology", ["IL", "IFN", "TNF", "CD", "NK", "DC", "CAR", "TCR", "HLA",
                    "CTLA", "PD", "TIM", "LAG", "TIGIT", "MDSC", "TAM", "CAF",
                    "TME", "TIL", "TREG", "MHC", "BTK"]),
    ("Molecular Biology", ["CDK", "BRAF", "KRAS", "NRAS", "EGFR", "HER",
                            "VEGF", "PDGF", "FGF", "MET", "ALK", "STAT", "JAK",
                            "PARP", "DNA", "RNA", "TERT", "MDM"]),
    ("Diagnostics", ["ELISA", "PCR", "FISH", "IHC", "FACS", "NGS", "PSA",
                      "CEA", "AFP", "LDH", "TMB", "MSI"]),
    ("Medical Imaging", ["MRI", "CT", "PET", "SPECT", "FDG"]),
]

DEFAULT_ABBREV_CATEGORY = "Molecular Biology"

# Optional: an importable Python list of (keyword, full_name, category,
# wikipedia_title) tuples for hand-curated abbreviations, if you have them.
# Leave as None to skip hand-curation and let every abbreviation go straight
# to Stage 5 for LLM enrichment — this is the recommended default for a new
# textbook; hand-curating definitions is optional polish, not a requirement.
ABBREV_DEFS_MODULE = None    # e.g. "my_abbrev_defs" (must define a list named ABBREVS)


# ─────────────────────────────────────────────────────────────────────────────
# 6. ABBREVIATION ENRICHMENT (Stage 5) — external knowledge lookup
# ─────────────────────────────────────────────────────────────────────────────

# "gene_ontology" | "none"  — extend with your own lookup in 05_abbrev_enrich/
# if you have a subject-appropriate reference API (MeSH, ICD-11, a legal
# terms database, etc.)
KNOWLEDGE_API = "gene_ontology"

# Only query the knowledge API for abbreviations assigned one of these
# categories (saves time/requests on categories where it won't help).
KNOWLEDGE_API_CATEGORIES = {"Molecular Biology", "Genetics", "Immunology"}


# ─────────────────────────────────────────────────────────────────────────────
# 7. LLM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

LLM_PROVIDER = "openai"            # only "openai" is implemented in these scripts
WIKI_REWRITE_MODEL = "gpt-4o-mini"  # Stage 3 — use "gpt-4o" for higher quality, higher cost
ABBREV_ENRICH_MODEL = "gpt-4o-mini"  # Stage 5
