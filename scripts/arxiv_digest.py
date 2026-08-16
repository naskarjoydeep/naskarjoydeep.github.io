#!/usr/bin/env python3
"""
arXiv Digest — daily feed generator. No AI, no API key required.

Pulls new arXiv submissions in a configurable set of categories, keeps the
ones that match a topic keyword list, and publishes each one with its own
abstract — title, authors, categories, link, and the abstract text exactly
as the authors wrote it. No summarization, no critique, no external API of
any kind: just arXiv's free public API plus local keyword matching.

Run manually with:  python scripts/arxiv_digest.py
Requires:            nothing but the packages in requirements.txt.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# --------------------------------------------------------------------------
# CONFIGURATION — edit freely, no need to touch anything below this block.
# --------------------------------------------------------------------------

# arXiv categories to scan. See https://arxiv.org/category_taxonomy
CATEGORIES = [
    "hep-th",         # holography, gauge theory, Seiberg-Witten, string theory
    "gr-qc",          # quantum gravity, de Sitter holography
    "quant-ph",       # entanglement, quantum error correction, quantum info
    "math-ph",        # spectral curves, operator-algebraic methods
    "math.OA",        # von Neumann algebras, subfactors, crossed products
    "math.QA",        # q-deformation, quantum groups
    "cond-mat.str-el",# topological order, Levin-Wen / string-net models
]

# A paper is kept if its title+abstract contains any of these (case-insensitive,
# dash-normalized). Grouped by theme purely for readability — edit any group.
KEYWORDS = [
    # holographic entanglement / QI (your core publication record)
    "holographic entanglement", "entanglement entropy", "entanglement wedge",
    "entropy inequalit", "entanglement of purification", "multipartite entanglement",
    "holographic code", "quantum error correcting code", "quantum error correction",
    "AdS/CFT", "bulk reconstruction", "tripartite information",
    "topological entanglement entropy", "contraction map", "holographic entropy cone",
    # de Sitter / SYK / gravity
    "de Sitter", "dSSYK", "Sachdev-Ye-Kitaev", "SYK model", "JT gravity",
    # q-deformed WP volumes / topological recursion (current project)
    "Weil-Petersson", "topological recursion", "Eynard-Orantin", "spectral curve",
    "q-deformed", "matrix model", "moduli space of curves",
    # Yang-Mills / operator algebras / QEC reformulation
    "von Neumann algebra", "type III factor", "subfactor", "crossed product",
    "modular flow", "Tomita-Takesaki", "operator algebra", "Yang-Mills",
    "Seiberg-Witten", "class S theory",
    # topological order
    "Levin-Wen", "topological order", "string-net",
    # scale/conformal invariance, CFT from neural nets
    "conformal invariance", "scale invariance", "conformal field theory",
    "neural network",
]

# How many days of arXiv submissions to scan on each run (>=2 covers weekends
# and any arXiv processing lag; already-seen IDs are skipped automatically).
DAYS_BACK = 3

# How many days of digest history to keep on the page.
KEEP_DAYS = 30

# Contact info arXiv asks API users to include. Fill in your own email.
USER_AGENT = "arxiv-digest-personal-feed/1.0 (contact: your-email@example.com)"

# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "data", "arxiv-digest.json")
HTML_PATH = os.path.join(REPO_ROOT, "arxiv-digest.html")
ARXIV_API = "http://export.arxiv.org/api/query"


def normalize(s):
    return s.lower().replace("\u2013", "-").replace("\u2014", "-")


def matches_interests(title, abstract, keywords):
    text = normalize(title + " " + abstract)
    return [kw for kw in keywords if normalize(kw) in text]


def fetch_recent(category, days_back, session):
    """Fetch recent papers in a category, newest first, stopping once we pass the cutoff."""
    params = {
        "search_query": f"cat:{category}",
        "start": 0,
        "max_results": 300,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = session.get(ARXIV_API, params=params, timeout=30)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    results = []
    for entry in feed.entries:
        try:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if published < cutoff:
            break  # descending sort order means everything after this is older too
        results.append(entry)
    return results


def load_existing_data():
    if not os.path.exists(DATA_PATH):
        return []
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_data(entries):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.write("\n")


def prune_old(entries, keep_days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    return [e for e in entries if e.get("published", "0000-00-00") >= cutoff]


def update_html(entries):
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(entries, indent=2, ensure_ascii=False)
    pattern = re.compile(
        r'(<script type="application/json" id="digest-data">)(.*?)(</script>)',
        re.DOTALL,
    )
    new_html, count = pattern.subn(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), html)
    if count != 1:
        raise RuntimeError(f"Expected exactly one digest-data script tag in {HTML_PATH}, found {count}")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    existing = [e for e in load_existing_data() if e.get("id") != "example"]
    existing_ids = {e["id"] for e in existing}

    candidates = {}
    for cat in CATEGORIES:
        try:
            for entry in fetch_recent(cat, DAYS_BACK, session):
                arxiv_id = entry.id.split("/abs/")[-1]
                if arxiv_id in candidates:
                    candidates[arxiv_id]["categories"].add(cat)
                else:
                    candidates[arxiv_id] = {"entry": entry, "categories": {cat}}
        except requests.RequestException as e:
            print(f"Warning: failed to fetch category {cat}: {e}")
        time.sleep(3)  # be polite to arXiv's API

    new_entries = []
    for arxiv_id, data in candidates.items():
        if arxiv_id in existing_ids:
            continue
        entry = data["entry"]
        title = entry.title.replace("\n", " ").strip()
        abstract = entry.summary.replace("\n", " ").strip()
        matched = matches_interests(title, abstract, KEYWORDS)
        if not matched:
            continue

        authors = [a.name for a in getattr(entry, "authors", [])] or ["(authors unavailable)"]

        new_entries.append({
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "categories": sorted(data["categories"]),
            "published": entry.published[:10],
            "link": f"https://arxiv.org/abs/{arxiv_id}",
            "summary": abstract,
            "matched_terms": matched,
        })

    combined = existing + new_entries
    combined = prune_old(combined, KEEP_DAYS)
    combined.sort(key=lambda e: e.get("published", ""), reverse=True)

    save_data(combined)
    update_html(combined)

    print(f"Done. {len(new_entries)} new entr{'y' if len(new_entries)==1 else 'ies'} added, {len(combined)} total on the page.")


if __name__ == "__main__":
    main()
