#!/usr/bin/env python3
"""
Sync publications from INSPIRE-HEP into publications.html.

No API key required — INSPIRE's REST API is public. The script:
  1. Fetches the author record to discover the INSPIRE BAI (e.g. "J.Naskar.1").
  2. Queries the literature API for that author, trying several documented
     query syntaxes and using the first that returns results.
  3. Rewrites the embedded JSON block in publications.html.

Run manually with:  python scripts/inspire_publications.py
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

AUTHOR_RECID = "2044957"          # from https://inspirehep.net/authors/2044957
MAX_RECORDS = 250
USER_AGENT = "naskarjoydeep.github.io publication sync (contact: your-email@example.com)"

# Drop very long author lists down to this many names, then "et al."
MAX_AUTHORS_SHOWN = 8

# Lowercase surname used to (a) sanity-check query results and (b) make sure
# your own name is never cut off when a long author list gets truncated.
SELF_SURNAME = "naskar"

# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(REPO_ROOT, "publications.html")
API = "https://inspirehep.net/api"

FIELDS = ",".join([
    "titles", "authors.full_name", "arxiv_eprints", "dois",
    "publication_info", "earliest_date", "citation_count",
    "document_type", "control_number", "preprint_date",
    "abstracts", "inspire_categories",
])


def get_json(session, url, params=None):
    r = session.get(url, params=params, timeout=40)
    r.raise_for_status()
    return r.json()


def discover_bai(session):
    """Fetch the author record and pull out the INSPIRE BAI, if present."""
    try:
        data = get_json(session, f"{API}/authors/{AUTHOR_RECID}")
    except (requests.RequestException, ValueError) as e:
        print(f"Note: could not fetch author record ({e}); will fall back to recid queries.")
        return None
    meta = data.get("metadata", {})
    for entry in meta.get("ids", []):
        if entry.get("schema") == "INSPIRE BAI":
            bai = entry.get("value")
            print(f"Discovered INSPIRE BAI: {bai}")
            return bai
    print("Note: no BAI found on the author record; will fall back to recid queries.")
    return None


def fetch_literature(session, bai):
    """Try each query syntax in turn; return hits from the first that works."""
    queries = []
    if bai:
        queries.append(f"a {bai}")
    queries += [
        f"authors.recid:{AUTHOR_RECID}",
        f"a {AUTHOR_RECID}",
    ]

    for q in queries:
        params = {
            "q": q,
            "sort": "mostrecent",
            "size": MAX_RECORDS,
            "fields": FIELDS,
        }
        try:
            data = get_json(session, f"{API}/literature", params=params)
        except (requests.RequestException, ValueError) as e:
            print(f"Query {q!r} failed: {e}")
            continue

        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", 0)
        if not hits:
            print(f"Query {q!r} returned no records; trying next syntax.")
            continue

        # Sanity check: the author should actually appear on these records.
        # Guards against a query silently falling back to an unrelated result set.
        matching = sum(
            1 for h in hits
            if any("naskar" in a.get("full_name", "").lower()
                   for a in h.get("metadata", {}).get("authors", []))
        )
        if matching < max(1, len(hits) // 2):
            print(f"Query {q!r} returned {len(hits)} records but only {matching} list the author; "
                  "treating as a bad match and trying next syntax.")
            continue

        print(f"Query {q!r} succeeded: {len(hits)} records (total reported: {total}).")
        return hits

    raise SystemExit(
        "Could not retrieve publications with any query syntax. Check that "
        f"AUTHOR_RECID ({AUTHOR_RECID}) is correct and that INSPIRE is reachable."
    )


MATHML_RE = re.compile(r"<math\b[^>]*>(.*?)</math>", re.DOTALL | re.IGNORECASE)

# Operators/symbols INSPIRE emits as literal unicode inside <mo>/<mi>.
SYMBOL_MAP = {
    "\u2212": "-", "\u2013": "-", "\u2014": "-",      # minus / en / em dash
    "\u00d7": r"\times", "\u22c5": r"\cdot", "\u2217": "*",
    "\u2264": r"\leq", "\u2265": r"\geq", "\u2260": r"\neq",
    "\u2248": r"\approx", "\u223c": r"\sim", "\u2245": r"\cong",
    "\u2192": r"\to", "\u21d2": r"\Rightarrow", "\u2208": r"\in",
    "\u221e": r"\infty", "\u2202": r"\partial", "\u2207": r"\nabla",
    "\u222b": r"\int", "\u2211": r"\sum", "\u220f": r"\prod",
    "\u221a": r"\sqrt", "\u00b1": r"\pm", "\u2295": r"\oplus",
    "\u2297": r"\otimes", "\u2286": r"\subseteq", "\u2282": r"\subset",
    "\u222a": r"\cup", "\u2229": r"\cap",
}
for _name, _ch in [
    ("alpha", "\u03b1"), ("beta", "\u03b2"), ("gamma", "\u03b3"), ("delta", "\u03b4"),
    ("epsilon", "\u03b5"), ("zeta", "\u03b6"), ("eta", "\u03b7"), ("theta", "\u03b8"),
    ("iota", "\u03b9"), ("kappa", "\u03ba"), ("lambda", "\u03bb"), ("mu", "\u03bc"),
    ("nu", "\u03bd"), ("xi", "\u03be"), ("pi", "\u03c0"), ("rho", "\u03c1"),
    ("sigma", "\u03c3"), ("tau", "\u03c4"), ("phi", "\u03c6"), ("chi", "\u03c7"),
    ("psi", "\u03c8"), ("omega", "\u03c9"),
    ("Gamma", "\u0393"), ("Delta", "\u0394"), ("Theta", "\u0398"), ("Lambda", "\u039b"),
    ("Xi", "\u039e"), ("Pi", "\u03a0"), ("Sigma", "\u03a3"), ("Phi", "\u03a6"),
    ("Psi", "\u03a8"), ("Omega", "\u03a9"),
]:
    SYMBOL_MAP[_ch] = "\\" + _name


def _strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _map_symbols(text):
    out = []
    for ch in text:
        rep = SYMBOL_MAP.get(ch)
        if rep is None:
            out.append(ch)
        elif rep.startswith("\\"):
            # Trailing space so \leq followed by A doesn't become \leqA
            out.append(rep + " ")
        else:
            out.append(rep)
    return "".join(out)


def _mathml_node_to_latex(node):
    """Recursively turn one MathML element into a LaTeX fragment."""
    tag = _strip_ns(node.tag)
    kids = list(node)

    def kid(i):
        return _mathml_node_to_latex(kids[i]) if i < len(kids) else ""

    def own_text():
        return _map_symbols((node.text or "").strip())

    if tag in ("mi", "mn", "mo", "mtext"):
        out = own_text()
        # Multi-letter identifiers should be upright, as in \mathrm{AdS}
        if tag == "mi" and len(out) > 1 and out.isalpha():
            out = r"\mathrm{" + out + "}"
        return out
    if tag in ("mrow", "math", "mstyle", "semantics", "mpadded"):
        return "".join(_mathml_node_to_latex(k) for k in kids)
    if tag == "msup":
        return "{" + kid(0) + "}^{" + kid(1) + "}"
    if tag == "msub":
        return "{" + kid(0) + "}_{" + kid(1) + "}"
    if tag == "msubsup":
        return "{" + kid(0) + "}_{" + kid(1) + "}^{" + kid(2) + "}"
    if tag == "mfrac":
        return r"\frac{" + kid(0) + "}{" + kid(1) + "}"
    if tag == "msqrt":
        return r"\sqrt{" + "".join(_mathml_node_to_latex(k) for k in kids) + "}"
    if tag == "mroot":
        return r"\sqrt[" + kid(1) + "]{" + kid(0) + "}"
    if tag == "mover":
        return r"\overline{" + kid(0) + "}"
    if tag == "munder":
        return r"\underline{" + kid(0) + "}"
    if tag == "mspace":
        return " "
    if tag == "annotation":
        return ""  # alternate encodings; skip so they don't double up
    # Unknown element: fall back to concatenating whatever is inside it
    return own_text() + "".join(_mathml_node_to_latex(k) for k in kids)


def mathml_to_latex(fragment):
    """Convert one <math> inner fragment to LaTeX. Returns None if unparseable."""
    try:
        root = ET.fromstring("<math>" + fragment + "</math>")
    except ET.ParseError:
        return None
    latex = _mathml_node_to_latex(root).strip()
    latex = re.sub(r"\s+", " ", latex)
    return latex or None


def clean_text(s):
    """Normalize an INSPIRE title/abstract: MathML -> LaTeX, fix invisible chars."""
    if not s:
        return s

    def repl(match):
        latex = mathml_to_latex(match.group(1))
        if latex is None:
            # Unparseable: strip the markup rather than show raw tags
            return re.sub(r"<[^>]+>", "", match.group(1))
        return "$" + latex + "$"

    s = MATHML_RE.sub(repl, s)
    # Soft hyphens render as nothing, turning "fractal-like" into "fractallike"
    s = s.replace("\u00ad", "-")
    s = s.replace("\u200b", "")           # zero-width space
    s = s.replace("\u00a0", " ")          # non-breaking space
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def format_author(full_name):
    """INSPIRE gives 'Bao, Ning'; render it as 'N. Bao' to match the site style."""
    if "," not in full_name:
        return full_name.strip()
    surname, _, given = full_name.partition(",")
    surname = surname.strip()
    given = given.strip()
    if not given:
        return surname
    initials = []
    for part in given.replace(".", " ").split():
        if part:
            initials.append(part[0].upper() + ".")
    return (" ".join(initials) + " " + surname).strip() if initials else surname


# Only these INSPIRE document types are listed. INSPIRE tags journal articles
# and arXiv preprints alike as "article", so this keeps both while dropping
# theses, conference proceedings, book chapters, reports, and notes.
ALLOWED_DOCUMENT_TYPES = {"article"}

# A record carrying any of these is dropped even if it is also tagged
# "article" — proceedings published in a journal are the common case.
EXCLUDED_DOCUMENT_TYPES = {
    "thesis", "book", "book chapter", "proceedings", "conference paper",
    "note", "report", "activity report",
}


def is_journal_article(hit):
    """True if the record should appear on the publications page."""
    types = {str(t).lower() for t in (hit.get("metadata", {}).get("document_type") or [])}
    if not types:
        # INSPIRE nearly always sets this; if it's absent, keep the record
        # rather than silently dropping a real paper.
        return True
    if types & EXCLUDED_DOCUMENT_TYPES:
        return False
    return bool(types & ALLOWED_DOCUMENT_TYPES)


def parse_record(hit):
    m = hit.get("metadata", {})

    titles = m.get("titles") or [{}]
    title = clean_text(titles[0].get("title", "")) or "(untitled)"

    raw_authors = [a.get("full_name", "") for a in m.get("authors", []) if a.get("full_name")]
    authors = [format_author(a) for a in raw_authors]
    if len(authors) > MAX_AUTHORS_SHOWN:
        shown = authors[:MAX_AUTHORS_SHOWN]
        # Never let truncation drop your own name off the list.
        if not any(SELF_SURNAME in a.lower() for a in shown):
            self_names = [a for a in authors if SELF_SURNAME in a.lower()]
            if self_names:
                shown = shown[:MAX_AUTHORS_SHOWN - 1] + [self_names[0]]
        authors = shown + ["et al."]

    arxiv = None
    categories = []
    eprints = m.get("arxiv_eprints") or []
    if eprints:
        arxiv = eprints[0].get("value")
        categories = list(eprints[0].get("categories") or [])

    # Abstracts: several may be present (arXiv, publisher, ...). Prefer the
    # arXiv one for consistency, since that's what the categories describe.
    abstract = None
    abstracts = m.get("abstracts") or []
    if abstracts:
        preferred = next(
            (a for a in abstracts if (a.get("source") or "").lower() == "arxiv"),
            abstracts[0],
        )
        abstract = clean_text(preferred.get("value") or "") or None

    doi = None
    dois = m.get("dois") or []
    if dois:
        doi = dois[0].get("value")

    # Journal reference, when published
    journal = None
    year = None
    for info in (m.get("publication_info") or []):
        jt = info.get("journal_title")
        if jt:
            vol = info.get("journal_volume", "")
            page = info.get("page_start") or info.get("artid") or ""
            yr = info.get("year")
            bits = jt
            if vol:
                bits += f" {vol}"
            if yr:
                bits += f" ({yr})"
            if page:
                bits += f" {page}"
            journal = bits
            if yr:
                year = str(yr)
            break

    if not year:
        date = m.get("earliest_date") or m.get("preprint_date") or ""
        year = date[:4] if len(date) >= 4 else "Unpublished"

    return {
        "title": title,
        "authors": authors,
        "arxiv": arxiv,
        "categories": categories,
        "abstract": abstract,
        "doi": doi,
        "journal": journal,
        "year": year,
        "citations": m.get("citation_count", 0),
        "inspire_id": m.get("control_number"),
    }


def compute_h_index(citation_counts):
    h = 0
    for i, c in enumerate(sorted(citation_counts, reverse=True), start=1):
        if c >= i:
            h = i
        else:
            break
    return h


def update_html(payload):
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    pattern = re.compile(
        r'(<script type="application/json" id="publications-data">)(.*?)(</script>)',
        re.DOTALL,
    )
    new_html, count = pattern.subn(lambda mo: mo.group(1) + "\n" + blob + "\n" + mo.group(3), html)
    if count != 1:
        raise RuntimeError(f"Expected one publications-data block in {HTML_PATH}, found {count}")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    bai = discover_bai(session)
    hits = fetch_literature(session, bai)

    kept, dropped = [], []
    for h in hits:
        (kept if is_journal_article(h) else dropped).append(h)

    if dropped:
        print(f"Excluded {len(dropped)} non-article record(s):")
        for h in dropped:
            md = h.get("metadata", {})
            t = (md.get("titles") or [{}])[0].get("title", "(untitled)")
            dt = ", ".join(md.get("document_type") or []) or "no document_type"
            print(f"  - [{dt}] {t[:70]}")

    records = [parse_record(h) for h in kept]
    # Newest first; unpublished/undated sort last
    records.sort(key=lambda r: (r["year"] if r["year"].isdigit() else "0000"), reverse=True)

    cites = [r["citations"] or 0 for r in records]
    payload = {
        "synced": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "summary": {
            "papers": len(records),
            "citations": sum(cites),
            "h_index": compute_h_index(cites),
        },
        "records": records,
    }

    update_html(payload)
    print(f"Done. {len(records)} publications, {sum(cites)} citations, h-index {payload['summary']['h_index']}.")


if __name__ == "__main__":
    main()
