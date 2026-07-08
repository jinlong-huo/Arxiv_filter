#!/usr/bin/env python3
"""Rename PDF papers to Zotero format: Author_Year_Title.pdf"""

import json
import os
import re
import sys
import time
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = '/Users/Vir-G/Downloads/Paper'
EXCLUDE_DIR = '_archive_seismic'
DRY_RUN = False  # Set to False to actually rename
INTERACTIVE = False  # Prompt user for metadata on remaining unknowns

# Track API calls for rate limiting
_arxiv_last_call = 0
_ARXIV_DELAY = 1.0  # seconds between arXiv API calls
_S2_LAST_CALL = 0
_S2_DELAY = 1.0  # seconds between Semantic Scholar calls
_S2_LIMIT = 90  # S2 allows ~100 per 5 min, be conservative

def sanitize(s):
    """Convert string to safe filename component"""
    if not s:
        return 'Unknown'
    s = s.strip()
    # Replace chars invalid for macOS filenames
    s = re.sub(r'[\\/:*?"<>|]', '', s)
    # Replace problematic chars with underscore
    bad_chars = r"[\s\-–—,;.!@#$%^&*()+=\[\]{}|~`'\"]+"
    s = re.sub(bad_chars, '_', s)
    # Remove non-ASCII
    s = re.sub(r'[^\x00-\x7F]+', '', s)
    # Collapse underscores
    s = re.sub(r'_+', '_', s)
    s = s.strip('_')
    if len(s) > 180:
        s = s[:177] + '...'
    return s or 'Unknown'


def is_zotero_format(filename):
    """Check if file already follows Author_Year_Title pattern"""
    # Allow underscores in author name (e.g., Author_et_al, GLM_Team)
    m = re.match(r'^([A-Za-z][a-zA-Z\-]+(?:_[a-zA-Z][a-zA-Z\-]*)*)_(\d{4})_', filename)
    if not m:
        return False
    name = m.group(1)
    # Reject all-caps "names" that are clearly acronyms from titles
    if name == name.upper() and len(name) <= 6:
        return False
    # Reject known bad "author" names (check first segment before any underscore)
    first_seg = name.split('_')[0].lower()
    bad_authors = {'unknown', 'anonymous', 'shapes', 'for', 'switch', 'networking',
                   'classification', 'signal', 'storage', 'math', 'design', 'computer',
                   'model', 'pre', 'artificial', 'chat', 'guage'}
    if first_seg in bad_authors:
        return False
    return True


def extract_arxiv_id(filename):
    """Extract arXiv ID from filename (true arXiv format: YYMM.NNNNN)"""
    # True arXiv IDs: (07-26)(01-12).NNNNN or NNNN
    m = re.search(r'(?:0[7-9]|1\d|2[0-6])(?:0[1-9]|1[0-2])\.(\d{4,5})(?:v\d+)?', filename)
    if m:
        return m.group(0).split('v')[0]  # return full ID without version
    return None


def extract_candidate_title(filename):
    """Extract a plausible paper title from a filename stem for search queries.

    Handles several patterns:
      - Unknown_YYYY_Unknown_YYYY_Author_et_al_YYYY_Actual_Title  (bad prior rename)
      - Author_et_al_2025_Title_With_Underscores                  (download_papers output)
      - Author - Year - Title                                     (descriptive filename)
      - Title with arXiv ID suffix (e.g. Title_2606.12345)
      - Generic PDF filename (use as-is)
    """
    stem = filename.rsplit('.', 1)[0]
    stem = re.sub(r'v\d+$', '', stem)  # strip version suffix

    # --- Strip "Unknown_YYYY_Unknown_YYYY_" prefix from prior bad renames ---
    m = re.match(r'^Unknown_\d{4}_Unknown_\d{4}_(.+)$', stem, re.IGNORECASE)
    if m:
        stem = m.group(1)

    # --- Strip leading arXiv ID ---
    stem = re.sub(r'^\d{4}\.\d{4,5}v?\d*_', '', stem)

    # --- Pattern: Author_et_al_YYYY_Rest -> keep Rest as title ---
    # Handles: De_Marchi_et_al_2025_Title..., GLM_Team_2024_Title..., Author_2023_Title...
    m = re.match(r'^[A-Za-z][a-zA-Z\-]+(?:_[a-zA-Z][a-zA-Z\-]*)*(?:_et_al)?_(\d{4})_(.+)$', stem)
    if m:
        return m.group(2)  # the title portion after year

    # --- Pattern: Author - Year - Venue - Title or Author - Year - Title ---
    m = re.match(r'^.+?\s+-\s+\d{4}\s+-\s+(?:[^-]+?\s+-\s+)?(.+)$', stem)
    if m:
        return m.group(1).strip()

    # --- Pattern: words with a year embedded (e.g. kleyko2018Title) ---
    m = re.match(r'^[a-zA-Z]+\d{4}(.+)$', stem)
    if m:
        return m.group(1)

    # --- Fallback: use the whole stem, strip common noise ---
    stem = re.sub(r'\b\d{4}\.\d{4,5}v?\d*\b', '', stem)  # remove arXiv IDs
    stem = re.sub(r'_{2,}', '_', stem).strip('_')
    return stem if len(stem) > 10 else None


def query_semantic_scholar(title, max_retries=1):
    """Search Semantic Scholar by title. Returns (author, year, title) or (None, None, None).

    Free tier: ~100 requests per 5 minutes. Handles 429 with backoff.
    """
    global _S2_LAST_CALL

    if not title or len(title) < 10:
        return None, None, None

    # Rate limiting
    elapsed = time.time() - _S2_LAST_CALL
    if elapsed < _S2_DELAY:
        time.sleep(_S2_DELAY - elapsed)

    query = urllib.parse.quote(title[:300].replace('_', ' '))
    url = (f'https://api.semanticscholar.org/graph/v1/paper/search'
           f'?query={query}&limit=3&fields=title,year,authors')

    for attempt in range(max_retries + 1):
        _S2_LAST_CALL = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'PaperRenamer/2.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  S2 rate-limited, skipping remaining S2 calls", file=sys.stderr)
                return ('__RATELIMITED__', None, None)
            return None, None, None
        except Exception as e:
            if attempt < max_retries:
                print(f"  S2 error: {e}, retrying...", file=sys.stderr)
                time.sleep(3)
                continue
            return None, None, None

        papers = data.get('data', [])
        if not papers:
            return None, None, None

        # Score each result: title similarity + year validity
        best = None
        best_score = -1
        query_words = set(re.sub(r'[^a-z0-9\s]', '', title.lower().replace('_', ' ')).split())

        for paper in papers:
            p_title = paper.get('title', '')
            if not p_title:
                continue
            p_words = set(re.sub(r'[^a-z0-9\s]', '', p_title.lower()).split())
            overlap = len(query_words & p_words)
            jaccard = overlap / max(len(query_words | p_words), 1)

            year = paper.get('year')
            year_str = str(year) if year else ''
            is_recent = year and 1990 <= year <= 2030

            score = jaccard * 3 + (1.0 if is_recent else 0)
            if score > best_score and jaccard > 0.12:
                best_score = score
                best = paper

        if not best:
            return None, None, None

        p_title = best.get('title', '')
        year = str(best.get('year', '')) if best.get('year') else ''
        authors = best.get('authors', [])

        if authors:
            last_name = authors[0].get('name', '').split()[-1]
            last_name = re.sub(r'[^a-zA-Z\-]', '', last_name)
            if len(authors) > 2:
                last_name += '_et_al'
        else:
            last_name = 'Unknown'

        return last_name, year, p_title

    return None, None, None


def extract_pdf_text_first_page(filepath):
    """Extract first-page text and font sizes using pdfplumber or PyPDF2 fallback."""
    lines = []
    font_sizes = {}  # line_index -> max_font_size

    # Try pdfplumber first (better text + font info)
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            if pdf.pages:
                page = pdf.pages[0]
                words = page.extract_words(keep_blank_chars=False, extra_attrs=['fontname', 'size'])
                if words:
                    # Group words into lines by Y-coordinate
                    line_map = {}
                    for w in words:
                        y_key = round(w['top'], 1)
                        if y_key not in line_map:
                            line_map[y_key] = {'text': [], 'max_size': 0}
                        line_map[y_key]['text'].append(w['text'])
                        sz = w.get('size', 0) or 0
                        if sz > line_map[y_key]['max_size']:
                            line_map[y_key]['max_size'] = sz

                    for y_key in sorted(line_map.keys()):
                        entry = line_map[y_key]
                        line_text = ' '.join(entry['text']).strip()
                        if line_text:
                            lines.append(line_text)
                            font_sizes[len(lines) - 1] = entry['max_size']
                    return lines, font_sizes

                # Fallback: plain text extraction
                text = page.extract_text()
                if text:
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    return lines, {}
    except Exception:
        pass

    # PyPDF2 fallback
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(filepath)
        page = reader.pages[0]
        text = page.extract_text()
        if text:
            lines = [l.strip() for l in text.split('\n') if l.strip()]
    except Exception:
        pass

    return lines, {}


def extract_pdf_metadata_enhanced(filepath):
    """Enhanced PDF metadata extraction using pdfplumber font sizes + heuristics."""
    lines, font_sizes = extract_pdf_text_first_page(filepath)
    if not lines or len(lines) < 3:
        return None, None, None

    title = None
    author = None
    year = None

    # --- Title: find line with largest font size in top half of first page ---
    if font_sizes:
        # Only consider first 20 lines for title
        title_candidates = [(i, sz) for i, sz in font_sizes.items() if i < min(20, len(lines))]
        title_candidates.sort(key=lambda x: x[1], reverse=True)
        for idx, sz in title_candidates:
            line = lines[idx]
            if len(line) < 5 or len(line) > 400:
                continue
            if _is_bad_title_line(line):
                continue
            if _is_bad_author_line(line):
                continue
            title = line
            break

    # Without font sizes, use first non-bad line
    if not title:
        for line in lines[:8]:
            if len(line) < 5 or len(line) > 400:
                continue
            if _is_bad_title_line(line):
                continue
            if _is_bad_author_line(line):
                continue
            title = line
            break

    # ── Author extraction ──────────────────────────────────────
    title_words = set()
    title_font_size = 0
    if title:
        title_words = {w.lower() for w in title.split()
                       if len(w) > 2 and w.lower() not in
                       {'the', 'and', 'for', 'with', 'via', 'its'}}
        # Find font size of the title line for continuation detection
        for i, line in enumerate(lines):
            if line == title and i in font_sizes:
                title_font_size = font_sizes[i]
                break

    # Determine where the title ends in the line list
    title_end = 0
    if title:
        for i, line in enumerate(lines):
            if line == title:
                title_end = i
                break

    for i in range(title_end + 1, min(title_end + 20, len(lines))):
        line = lines[i]
        if len(line) < 5 or len(line) > 400:
            continue
        if _is_bad_author_line(line):
            continue
        if _is_bad_title_line(line):
            continue
        # Skip title-continuation lines (same font size as title)
        if title_font_size > 0 and i in font_sizes:
            if abs(font_sizes[i] - title_font_size) < title_font_size * 0.1:
                continue
        if title_words:
            line_words = {w.lower() for w in line.split()
                          if len(w) > 2 and w.lower() not in
                          {'the', 'and', 'for', 'with', 'via', 'its'}}
            if line_words:
                overlap = len(line_words & title_words) / max(len(line_words), 1)
                if overlap >= 0.4:
                    continue
        if _looks_like_author_line(line):
            author = line
            break

    # ── Year extraction (header area only, before abstract) ────
    for line in lines[:8]:
        m = re.search(r'\b((?:19|20)\d{2})\b', line)
        if m:
            year = m.group(1)
            break
    if not year:
        for line in lines[:30]:
            m = re.search(r'\b((?:19|20)\d{2})\b', line)
            if m:
                year = m.group(1)
                break

    # Clean title
    if title:
        title = title.replace('\n', ' ').strip()
        title = re.sub(r'\s+', ' ', title)
        title = re.sub(r'\s*arXiv:\s*\d{4}\.\d{4,5}v?\d*\s*$', '', title)

    last_name = None
    if author:
        last_name = _extract_author_last_name(author)

    return last_name, year, title


def query_arxiv_batch(arxiv_ids):
    """Query arXiv API for up to 100 IDs at once. Returns dict of id -> (author, year, title)."""
    global _arxiv_last_call

    # Rate limiting
    elapsed = time.time() - _arxiv_last_call
    if elapsed < _ARXIV_DELAY:
        time.sleep(_ARXIV_DELAY - elapsed)

    id_list = ','.join(arxiv_ids[:100])
    url = f'https://export.arxiv.org/api/query?id_list={id_list}&max_results=100'
    req = urllib.request.Request(url, headers={'User-Agent': 'PaperRenamer/1.0'})
    _arxiv_last_call = time.time()

    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = resp.read().decode('utf-8')
    except Exception as e:
        print(f"  arXiv API error for batch: {e}", file=sys.stderr)
        return {}

    ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
    root = ET.fromstring(data)

    results = {}
    for entry in root.findall('atom:entry', ns):
        # Get arxiv ID from entry
        id_url = entry.find('atom:id', ns)
        if id_url is None:
            continue
        # Extract ID from http://arxiv.org/abs/1234.5678v1
        raw_id = id_url.text.strip().split('/abs/')[-1]
        clean_id = re.sub(r'v\d+$', '', raw_id)

        # Title
        title_elem = entry.find('atom:title', ns)
        title = title_elem.text.strip() if title_elem is not None else ''

        # Authors
        authors = []
        for author_elem in entry.findall('atom:author', ns):
            name_elem = author_elem.find('atom:name', ns)
            if name_elem is not None:
                authors.append(name_elem.text.strip())

        # Year from published date
        pub_elem = entry.find('atom:published', ns)
        year = pub_elem.text[:4] if pub_elem is not None else ''

        # Format first author
        if authors:
            first = authors[0]
            parts = first.split()
            last_name = parts[-1] if parts else first
            # Handle particles like "van der", "de la"
            if len(parts) >= 2 and parts[-2].lower() in ('van', 'von', 'de', 'del', 'la', 'der', 'den'):
                last_name = parts[-2] + '_' + parts[-1]
            if len(authors) > 2:
                last_name += '_et_al'
            # Clean last name
            last_name = re.sub(r'[^a-zA-Z_\-]', '', last_name)
        else:
            last_name = 'Unknown'

        results[clean_id] = (last_name, year, title)
        # Also add with version number
        results[raw_id] = (last_name, year, title)

    return results


def parse_author_year_title(filename):
    """Parse filename like 'Author et al - Year - Venue - Title.pdf'"""
    name = filename.rsplit('.', 1)[0]

    # Pattern 1: "Author1, Author2, ... et al - Year - Venue - Title"
    m = re.match(r'^(.+?)\s+-\s+(\d{4})\s+-\s+([^-]+?)\s+-\s+(.+)$', name)
    if m:
        author_part = m.group(1).strip()
        year = m.group(2)
        venue_or_title = m.group(3).strip()
        rest = m.group(4).strip()
        # Determine which is the actual title (usually the longer one)
        if len(venue_or_title) > len(rest):
            title = venue_or_title
        else:
            title = rest
        # Extract last name  
        last_name = _extract_author_last_name(author_part)
        return last_name, year, title

    # Pattern 2: "Author - Year - Title" (3 parts)
    m = re.match(r'^(.+?)\s+-\s+(\d{4})\s+-\s+(.+)$', name)
    if m:
        author_part = m.group(1).strip()
        year = m.group(2)
        title = m.group(3).strip()
        last_name = _extract_author_last_name(author_part)
        return last_name, year, title

    # Pattern 3: "Author - Venue - Title" (no year, but has venue)
    # Try to find year elsewhere in filename
    m = re.match(r'^(.+?)\s+-\s+([^-]+?)\s+-\s+(.+)$', name)
    if m:
        author_part = m.group(1).strip()
        venue = m.group(2).strip()
        rest = m.group(3).strip()
        title = rest
        
        # Try to find year in the filename
        year = None
        ym = re.search(r'(19|20)\d{2}', filename)
        if ym:
            year = ym.group(0)
        
        last_name = _extract_author_last_name(author_part)
        return last_name, year, title

    # Pattern 4: "Name-like words with year embedded"
    # e.g., "kleyko2018.pdf"
    m = re.match(r'^([a-zA-Z]+)(\d{4})(.*)', name)
    if m:
        last_name = m.group(1).capitalize()
        year = m.group(2)
        title = name  # use full name as title fallback
        return last_name, year, title

    return None, None, None


def _is_bad_title_line(line):
    """Check if a line looks like journal/venue meta, not a real title"""
    bad_patterns = [
        r'^Journal of Machine Learning Research',
        r'^Published as a conference paper at',
        r'^Proceedings of the',
        r'^This paper is included in the Proceedings',
        r'^JOURNAL OF LATEX CLASS FILES',
        r'^IEEE TRANSACTIONS ON',
        r'^arXiv:\d{4}\.\d{4,5}',
        r'^\d{4} IEEE',
        r'^Submitted \d+/\d+;',
        r'^Accepted \d+',
        r'^Received \d+',
        r'^Vol\. \d+',
        r'^\d+ \(\d{4}\) \d+',
    ]
    return any(re.match(p, line, re.IGNORECASE) for p in bad_patterns)


def _is_bad_author_line(line):
    """Check if a line is not a real author line"""
    if '@' in line:
        return True
    if re.search(r'https?://', line):
        return True
    if re.match(r'^(Abstract|Introduction|Related Work|Background|Motivation)', line, re.IGNORECASE):
        return True
    # Lines that are obviously journal/venue/date info
    if re.match(r'^(Journal of|Proceedings of|Published as|Submitted|Accepted|Received|Vol\.|IEEE|ACM)', line):
        return True
    # Month/date lines
    if re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', line):
        return True
    # Pure email usernames like "fedwardhu, yeshe, phwallis"
    if re.match(r'^[a-z][a-z0-9]+,\s*[a-z][a-z0-9]+', line):
        return True
    # Lines containing affiliation keywords
    if re.search(r'\b(University|Institute|College|Laboratory|School|Department|Division|Center|Centre|Research|Academy|Corporation|Limited|Ltd|Inc)\b', line) and len(line.split()) <= 5:
        return True
    # Single-entity lines (just company names)
    if re.match(r'^(University|Department|Institute|College|School|Laboratory|Research|Google|Meta|Microsoft|OpenAI|Amazon|Apple|IBM|Intel|NVIDIA|arXiv|Cornell|Stanford|MIT|UC\s|Skolkovo|Skolktech)$', line.strip()):
        return True
    return False


def _looks_like_author_line(line):
    """Check if a line looks like it could contain author names"""
    # Clean superscripts first
    cleaned = re.sub(r'[\d*†‡§¶#©®™★✉\d]+', ' ', line).strip()
    # Split by commas, semicolons, AND spaces for concatenated-name detection
    words = re.split(r'[,\s]+', cleaned)
    words = [w for w in words if w and w[0].isupper()]
    if len(words) < 2:
        # Also check for CamelCase concatenation: "SamyamRajbhandari" has uppercase words
        camel = re.findall(r'[A-Z][a-z]+', line)
        if len(camel) >= 2:
            return True
        return False
    # Count capitalized words (names)
    return len(words) >= 2 and len(words) >= min(len(cleaned.split()), 3) * 0.5


def _extract_author_last_name(author_text):
    """Extract first author's last name from author text, add _et_al if multi-author"""
    # Clean superscripts and special chars
    author_text = re.sub(r'[\d*†‡§¶#©®™★✉]+', '', author_text).strip()
    author_text = re.sub(r'\s+', ' ', author_text)
    
    # Bad words that shouldn't be treated as author names
    bad_names = {
        'submission', 'models', 'systems', 'example', 'tasks', 'methods',
        'series', 'efficiency', 'recently', 'trends', 'architectures',
        'proceedings', 'papers', 'intelligence', 'computing', 'sensing',
        'networking', 'management', 'processing', 'identifying', 'placement',
        'generation', 'laboratory', 'unknown', 'inc', 'user', 'data',
        'submitted', 'accepted', 'published', 'received', 'vol', 'pp',
        'journal', 'science', 'technology', 'engineering', 'department',
        'university', 'institute', 'college', 'school', 'research',
        'however', 'recently', 'today', 'years', 'ends', 'space',
        'fini', 'submission', 'china', 'america', 'seattle', 'beijing',
        'shanghai', 'irvine', 'california', 'essex', 'fast', 'april',
        'september', 'july', 'may', 'june', 'august', 'october',
        'november', 'december', 'january', 'february', 'march',
        'presented', 'networks', 'files', 'identifying', 'cong',
        'switch', 'networking', 'architectures', 'routing',
    }
    
    # Split by common separators
    first_author = re.split(r'[,;]\s*|\s+and\s+', author_text)[0].strip()
    # Remove common prefixes
    first_author = re.sub(r'^(by|By|and)\s+', '', first_author)
    
    # Check if it contains "et al"
    has_et_al = bool(re.search(r'\bet\s*al\b', author_text))
    
    parts = first_author.split()
    if not parts:
        return None
    
    # Get last name
    last_name = parts[-1]
    # Handle particles
    if len(parts) >= 2 and parts[-2].lower() in ('van', 'von', 'de', 'del', 'la', 'der', 'den', 'el', 'al'):
        last_name = parts[-2] + '_' + parts[-1]
    last_name = re.sub(r'[^a-zA-Z\-_]', '', last_name)
    
    # If last name is a bad word, try second-to-last
    if last_name.lower() in bad_names and len(parts) >= 2:
        last_name = parts[-2]
        last_name = re.sub(r'[^a-zA-Z\-]', '', last_name)
    # If still bad, try first name
    if last_name.lower() in bad_names and len(parts) >= 1:
        last_name = parts[0]
        last_name = re.sub(r'[^a-zA-Z\-]', '', last_name)
    
    if not last_name or len(last_name) < 2:
        return None
    
    # Check if multi-author
    if ',' in author_text or ';' in author_text or ' and ' in author_text.lower() or has_et_al:
        if not last_name.endswith('_et_al'):
            last_name += '_et_al'
    
    return last_name


def get_file_year(filepath):
    """Get year from file modification time (fallback for untitled files)"""
    try:
        mtime = os.path.getmtime(filepath)
        return time.strftime('%Y', time.localtime(mtime))
    except Exception:
        return None


def extract_pdf_metadata(filepath):
    """Try PyPDF2 metadata and first-page text extraction"""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(filepath)
        meta = reader.metadata

        author = ''
        title = ''
        year = ''

        if meta:
            author = (meta.get('/Author', '') or '').strip()
            title = (meta.get('/Title', '') or '').strip()

            # Try creation date for year
            creation = meta.get('/CreationDate', '')
            if creation and creation.startswith('D:'):
                y = re.search(r'D:(\d{4})', creation)
                if y:
                    year = y.group(1)

        # If no metadata from PDF info, try first page text
        if not title or not author or not year:
            try:
                page = reader.pages[0]
                text = page.extract_text()
                if text and len(text) > 20:
                    lines = [l.strip() for l in text.split('\n') if l.strip()]

                    # --- Title extraction ---
                    if not title:
                        for line in lines[:5]:
                            if len(line) < 5 or len(line) > 300:
                                continue
                            if _is_bad_title_line(line):
                                continue
                            if _is_bad_author_line(line):
                                continue
                            # Found a plausible title
                            title = line
                            break

                    # --- Author extraction ---
                    if not author:
                        # Build a set of meaningful words from the title to detect
                        # title-continuation lines (e.g., "Quantum Data Centers
                        # with Switch Networks" right after the title).
                        title_words = set()
                        if title:
                            title_words = {w.lower() for w in title.split()
                                           if len(w) > 2 and w.lower() not in
                                           {'the', 'and', 'for', 'with', 'via', 'its'}}

                        for line in lines[1:15]:
                            if len(line) < 5 or len(line) > 300:
                                continue
                            if _is_bad_author_line(line):
                                continue
                            if _is_bad_title_line(line):
                                continue
                            # Skip lines that share ≥40% of their meaningful
                            # words with the title — these are title continuations
                            if title_words:
                                line_words = {w.lower() for w in line.split()
                                              if len(w) > 2 and w.lower() not in
                                              {'the', 'and', 'for', 'with', 'via', 'its'}}
                                if line_words:
                                    overlap = len(line_words & title_words) / len(line_words)
                                    if overlap >= 0.4:
                                        continue
                            # Author lines should look like names (mostly capitalized words)
                            if _looks_like_author_line(line):
                                author = line
                                break

                    # --- Year extraction ---
                    if not year:
                        for line in lines[:20]:
                            # Look for year in patterns like "(2016)" or "20XX"
                            m = re.search(r'\(?(19|20)\d{2}\)?', line)
                            if m:
                                year = m.group(0).strip('()')
                                break
            except Exception:
                pass

        # Clean title
        if title:
            title = title.replace('\n', ' ').strip()
            title = re.sub(r'\s+', ' ', title)
            # Strip any trailing arXiv IDs
            title = re.sub(r'\s*arXiv:\s*\d{4}\.\d{4,5}v?\d*\s*$', '', title)

        # Extract author last name
        last_name = None
        if author:
            last_name = _extract_author_last_name(author)

        return last_name, year, title
    except Exception:
        return None, None, None


def get_new_name(filepath, arxiv_batch_cache, s2_count=None):
    """Determine new filename for a single PDF.

    Strategy chain:
      1. arXiv API          — most reliable, needs arXiv ID in filename
      2. PDF text (enhanced) — pdfplumber + font-size heuristics (fast, local)
      3. Semantic Scholar   — title search fallback (rate-limited, ~100/5min)
      4. PDF text (legacy)  — PyPDF2 metadata + basic text (covers edge cases)
      5. Filename parsing   — regex matching on descriptive filenames
      6. Year fallbacks     — file mtime, arXiv ID prefix, regex
    """
    dirname = os.path.dirname(filepath)
    filename = os.path.basename(filepath)

    if is_zotero_format(filename):
        return None, 'SKIP_ALREADY_OK'

    author = None
    year = None
    title = None

    # ── Strategy 1: arXiv API (for arXiv ID files) ──────────────
    arxiv_id = extract_arxiv_id(filename)
    if arxiv_id and arxiv_id in arxiv_batch_cache:
        author, year, title = arxiv_batch_cache[arxiv_id]

    # ── Strategy 2: Enhanced PDF text (pdfplumber + heuristics) ──
    if not (author and title) or not year:
        a, y, t = extract_pdf_metadata_enhanced(filepath)
        if not author:
            author = a
        if not year:
            year = y
        if not title:
            title = t

    # ── Strategy 3: Semantic Scholar (title search) ──────────────
    if not (author and title):
        can_title = extract_candidate_title(filename)
        if not can_title and title:
            can_title = title
        if can_title:
            quota_ok = s2_count is None or s2_count[0] < _S2_LIMIT
            if quota_ok:
                a, y, t = query_semantic_scholar(can_title)
                if s2_count is not None:
                    s2_count[0] += 1
                if a == '__RATELIMITED__':
                    # S2 rate-limited — skip all remaining S2 calls this run
                    if s2_count is not None:
                        s2_count[0] = _S2_LIMIT
                elif a and t:
                    author = a
                    if not year:
                        year = y
                    title = t
            else:
                print(f"  S2 quota ({_S2_LIMIT}/5min) reached, skipping",
                      file=sys.stderr)

    # ── Strategy 4: Legacy PDF metadata (PyPDF2) ─────────────────
    if not (author and title) or not year:
        a, y, t = extract_pdf_metadata(filepath)
        if not author:
            author = a
        if not year:
            year = y
        if not title:
            title = t

    # ── Strategy 5: Parse descriptive filename ───────────────────
    if not (author and title):
        a, y, t = parse_author_year_title(filename)
        if not author:
            author = a
        if not year:
            year = y
        if not title:
            title = t

    # Year fallback: file modification time (newest = recently downloaded)
    if not year or year == '0000':
        fy = get_file_year(filepath)
        if fy:
            year = fy
    # Year fallback: extract from arXiv ID (YYMM -> 20YY)
    if not year or year == '0000':
        arxiv_id = extract_arxiv_id(filename)
        if arxiv_id and len(arxiv_id) >= 2:
            yy = arxiv_id[:2]
            if yy.isdigit():
                year = '20' + yy
    # Year fallback: any 4-digit 20XX or 19XX in filename
    if not year or year == '0000':
        ym = re.search(r'(19|20)(\d{2})', filename)
        if ym:
            year = ym.group(0)

    # Title fallback: use filename as title
    if not title:
        title = filename.rsplit('.', 1)[0]

    if not author:
        author = 'Unknown'
    if not year:
        year = '0000'

    # Sanitize
    author_s = sanitize(author)
    title_s = sanitize(title)
    year_s = re.sub(r'[^0-9]', '', year)[:4] or '0000'

    new_name = f"{author_s}_{year_s}_{title_s}.pdf"
    new_path = os.path.join(dirname, new_name)

    # Handle duplicate filenames in same directory
    if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(filepath):
        base = new_name.rsplit('.', 1)[0]
        counter = 1
        while True:
            new_name = f"{base}_{counter}.pdf"
            new_path = os.path.join(dirname, new_name)
            if not os.path.exists(new_path):
                break
            counter += 1

    return new_name, 'OK'


def interactive_prompt(filepath):
    """Ask the user for metadata on a paper that couldn't be auto-renamed."""
    filename = os.path.basename(filepath)
    print(f"\n  Couldn't auto-detect metadata for:")
    print(f"    {filename}")
    print(f"  Enter metadata (leave blank to skip):")
    try:
        author = input("    Author (last name): ").strip()
        if not author:
            return None
        year = input("    Year (YYYY): ").strip()
        title = input("    Title: ").strip()
        if not title:
            title = filename.rsplit('.', 1)[0]
        if not year:
            year = '0000'
        author_s = sanitize(author)
        title_s = sanitize(title)
        year_s = re.sub(r'[^0-9]', '', year)[:4] or '0000'
        return f"{author_s}_{year_s}_{title_s}.pdf"
    except (EOFError, KeyboardInterrupt):
        return None


def main():
    # Collect all arXiv IDs first
    arxiv_ids = set()
    all_pdfs = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != EXCLUDE_DIR]
        for f in filenames:
            if f.lower().endswith('.pdf'):
                fp = os.path.join(dirpath, f)
                all_pdfs.append(fp)
                aid = extract_arxiv_id(f)
                if aid:
                    arxiv_ids.add(aid)

    print(f"Found {len(all_pdfs)} PDF files")
    print(f"Unique arXiv IDs: {len(arxiv_ids)}")

    # Batch query arXiv API
    print("\nQuerying arXiv API...")
    arxiv_cache = {}
    arxiv_list = sorted(arxiv_ids)
    for i in range(0, len(arxiv_list), 100):
        batch = arxiv_list[i:i + 100]
        results = query_arxiv_batch(batch)
        arxiv_cache.update(results)
        print(f"  Batch {i // 100 + 1}: got {len(results)} results for {len(batch)} IDs")
        if i + 100 < len(arxiv_list):
            time.sleep(1.5)  # Be nice to the API

    print(f"\nTotal arXiv metadata retrieved: {len(arxiv_cache)}")

    # Process each file (sorted by modification date, newest first)
    print("\nProcessing files (newest first)...")
    results = {'skip_ok': [], 'rename': [], 'error': []}

    # Collect files with timestamps for sorting
    file_entries = []
    for fp in all_pdfs:
        try:
            mtime = os.path.getmtime(fp)
        except Exception:
            mtime = 0
        file_entries.append((fp, mtime))

    # Sort: newest first (descending mtime)
    file_entries.sort(key=lambda x: x[1], reverse=True)

    s2_count = [0]  # mutable counter for Semantic Scholar rate limiting

    for fp, mtime in file_entries:
        new_name, status = get_new_name(fp, arxiv_cache, s2_count)
        ts = time.strftime('%Y-%m-%d', time.localtime(mtime)) if mtime else 'unknown'
        if status == 'SKIP_ALREADY_OK':
            results['skip_ok'].append(fp)
        elif new_name:
            results['rename'].append((fp, new_name))
            if DRY_RUN:
                basename = os.path.basename(fp)
                if len(results['rename']) <= 30:
                    print(f"  [{ts}] {basename[:80]}")
                    print(f"         -> {new_name}")
        else:
            # Interactive fallback for remaining unknowns
            if INTERACTIVE and not DRY_RUN:
                manual_name = interactive_prompt(fp)
                if manual_name:
                    results['rename'].append((fp, manual_name))
                    continue
            results['error'].append(fp)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY ({'(DRY RUN)' if DRY_RUN else 'LIVE MODE'})")
    print(f"  Already OK:      {len(results['skip_ok'])}")
    print(f"  Will rename:     {len(results['rename'])}")
    print(f"  Couldn't rename: {len(results['error'])}")

    if results['error'] and len(results['error']) < 20:
        print("\nCouldn't determine metadata for:")
        for fp in results['error']:
            try:
                ts = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(fp)))
            except Exception:
                ts = 'unknown'
            print(f"  [{ts}] {os.path.basename(fp)}")

    # Print rename preview for more files if in dry run
    if DRY_RUN and len(results['rename']) > 30:
        print(f"\nShowing all {len(results['rename'])} renames (newest first):")
        for fp, new_name in results['rename']:
            basename = os.path.basename(fp)
            try:
                ts = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(fp)))
            except Exception:
                ts = 'unknown'
            print(f"  [{ts}] {basename[:80]}")
            print(f"         -> {new_name}")

    if DRY_RUN:
        print("\nThis was a DRY RUN. To actually rename, set DRY_RUN = False")
    else:
        # Actual renaming
        print("\nRenaming files...")
        success = 0
        failures = 0
        for fp, new_name in results['rename']:
            dirname = os.path.dirname(fp)
            new_path = os.path.join(dirname, new_name)
            try:
                if os.path.exists(new_path):
                    print(f"  SKIP (target exists): {os.path.basename(fp)}")
                    failures += 1
                    continue
                os.rename(fp, new_path)
                success += 1
            except Exception as e:
                print(f"  FAIL: {os.path.basename(fp)} -> {e}")
                failures += 1
        print(f"\nRenamed: {success}, Failed: {failures}")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--go':
        DRY_RUN = False
        print("*** LIVE MODE - will actually rename files ***")
        resp = input("Type 'yes' to confirm: ")
        if resp.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    if '--interactive' in sys.argv:
        INTERACTIVE = True
        DRY_RUN = False
        print("*** INTERACTIVE MODE — will prompt for unknown papers ***")
    main()
