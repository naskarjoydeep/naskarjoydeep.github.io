#!/usr/bin/env python3
"""
News updates from INSPIRE — seminars and new papers.

Pulls two kinds of item into news.html:

  1. Seminars where you are listed as a speaker (INSPIRE /api/seminars)
  2. Papers that recently appeared or were published (INSPIRE /api/literature)

Identity is matched on your INSPIRE author record ID, not on your name, so
another person called Joydeep Naskar cannot be picked up by mistake. Where a
seminar record has no linked author profile, a fallback requires both a
surname match AND a known affiliation; anything else is skipped and reported.

Hand-written entries in news.html are never touched — this script only
rewrites the block marked `news-auto`.

Run:      python scripts/news_updates.py
Requires: requests
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

# --------------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------------

AUTHOR_RECID = "2044957"
SELF_SURNAME = "naskar"

# Fallback identity check, used only when a seminar has no linked author
# profile. A record must mention the surname AND one of these affiliations.
KNOWN_AFFILIATIONS = [
    "bimsa", "beijing institute of mathematical sciences",
    "northeastern", "kitp", "kavli institute for theoretical physics",
    "iaifi", "nsf ai institute", "pacific northwest", "pnnl",
    "niser", "national institute of science education",
]

# How far back to look for news items.
SEMINAR_LOOKBACK_DAYS = 540      # ~18 months of past seminars
PAPER_LOOKBACK_DAYS = 365        # papers from the last year

# Keep at most this many auto-generated items on the page.
MAX_ITEMS = 40

USER_AGENT = "naskarjoydeep.github.io news updater (contact: your-email@example.com)"

# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(REPO_ROOT, "news.html")
API = "https://inspirehep.net/api"


def get(session, path, params):
    r = session.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def ref_is_self(ref):
    """True if an INSPIRE $ref URL points at your author record."""
    if not ref:
        return False
    return re.search(rf"/authors/{AUTHOR_RECID}(?:$|[/?#])", str(ref)) is not None


def speaker_is_self(speaker):
    """
    Strict identity check for one speaker entry.
    Returns (is_self, how) where `how` explains which rule matched.
    """
    ref = (speaker.get("record") or {}).get("$ref")
    if ref:
        # Strongest signal: INSPIRE has linked this speaker to an author
        # profile. Trust it in both directions — a link to someone else is a
        # positive signal that this is NOT you.
        return (True, "author-profile link") if ref_is_self(ref) else (False, "linked to a different author")

    name = " ".join(filter(None, [speaker.get("first_name"), speaker.get("last_name")])) or speaker.get("name", "")
    if SELF_SURNAME not in name.lower():
        return (False, "surname does not match")

    affiliations = " ".join(
        (a.get("value") or "") for a in (speaker.get("affiliations") or [])
    ).lower()
    if any(k in affiliations for k in KNOWN_AFFILIATIONS):
        return (True, "surname + known affiliation")

    return (False, "surname matches but affiliation unrecognised")


def fetch_seminars(session):
    """Seminars with you as a speaker. Returns (items, skipped)."""
    since = (datetime.now(timezone.utc) - timedelta(days=SEMINAR_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    items, skipped = [], []

    try:
        data = get(session, "seminars", {
            "q": SELF_SURNAME,
            "size": 100,
            "sort": "dateasc",
            "fields": "title,speakers,start_datetime,end_datetime,series,"
                      "inspire_categories,control_number,address,join_urls",
        })
    except requests.RequestException as e:
        print(f"  ! seminar query failed: {e}")
        return items, skipped

    for hit in data.get("hits", {}).get("hits", []):
        m = hit.get("metadata", {})
        speakers = m.get("speakers") or []

        verdicts = [speaker_is_self(s) for s in speakers]
        matched = [v for v in verdicts if v[0]]
        if not matched:
            reasons = {v[1] for v in verdicts} or {"no speakers listed"}
            skipped.append(((m.get("title") or {}).get("title", "(untitled)"), "; ".join(sorted(reasons))))
            continue

        start = (m.get("start_datetime") or "")[:10]
        if start and start < since:
            continue

        title = (m.get("title") or {}).get("title", "(untitled)")
        series = ""
        series_list = m.get("series") or []
        if series_list:
            series = series_list[0].get("name", "")
        if not series:
            addr = m.get("address") or {}
            series = addr.get("place_name") or addr.get("cities", [""])[0] if addr else ""

        items.append({
            "date": start or "",
            "tag": "Talk",
            "text": (f"{series}: " if series else "") + f"\u201c{title}\u201d",
            "url": f"https://inspirehep.net/seminars/{m.get('control_number')}" if m.get("control_number") else None,
            "source": "INSPIRE seminars",
            "match": matched[0][1],
        })

    return items, skipped


def fetch_papers(session):
    """Recent papers, as news items. Identity comes from the author query itself."""
    since = (datetime.now(timezone.utc) - timedelta(days=PAPER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    items = []
    try:
        data = get(session, "literature", {
            "q": f"a {AUTHOR_RECID}",
            "size": 50,
            "sort": "mostrecent",
            "fields": "titles,publication_info,earliest_date,arxiv_eprints,"
                      "control_number,document_type,authors.recid",
        })
    except requests.RequestException as e:
        print(f"  ! paper query failed: {e}")
        return items

    for hit in data.get("hits", {}).get("hits", []):
        m = hit.get("metadata", {})

        # Re-verify identity rather than trusting the query string alone.
        recids = {str(a.get("recid")) for a in (m.get("authors") or []) if a.get("recid")}
        if recids and AUTHOR_RECID not in recids:
            continue

        types = {str(t).lower() for t in (m.get("document_type") or [])}
        if types and "article" not in types:
            continue

        date = (m.get("earliest_date") or "")[:10]
        if not date or date < since:
            continue

        title = ((m.get("titles") or [{}])[0]).get("title", "(untitled)")
        title = re.sub(r"<[^>]+>", "", title)  # strip any MathML markup
        title = title.replace("\u00ad", "-").strip()

        pub = (m.get("publication_info") or [{}])[0]
        journal = pub.get("journal_title")
        if journal:
            text = f"\u201c{title}\u201d published in {journal}."
            tag = "Paper"
        else:
            text = f"New preprint: \u201c{title}\u201d."
            tag = "Preprint"

        items.append({
            "date": date,
            "tag": tag,
            "text": text,
            "url": f"https://inspirehep.net/literature/{m.get('control_number')}" if m.get("control_number") else None,
            "source": "INSPIRE literature",
            "match": "author record id",
        })

    return items


def update_html(items):
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(
        {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "items": items},
        indent=2, ensure_ascii=False,
    )
    pattern = re.compile(
        r'(<script type="application/json" id="news-auto">)(.*?)(</script>)', re.DOTALL
    )
    new_html, n = pattern.subn(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), html)
    if n != 1:
        raise RuntimeError(f"Expected one news-auto block in {HTML_PATH}, found {n}")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    print("Fetching seminars...")
    seminars, skipped = fetch_seminars(session)
    print(f"  matched {len(seminars)}")
    if skipped:
        print(f"  skipped {len(skipped)} seminar(s) that mention the name but failed identity checks:")
        for title, reason in skipped[:10]:
            print(f"    - {title[:60]}  [{reason}]")

    print("Fetching papers...")
    papers = fetch_papers(session)
    print(f"  matched {len(papers)}")

    items = seminars + papers
    # De-duplicate on (date, text); newest first
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        key = (it["date"], it["text"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    unique = unique[:MAX_ITEMS]

    update_html(unique)
    print(f"Done. {len(unique)} automatic item(s) written to news.html.")
    if not unique:
        print("Note: nothing matched. Hand-written entries on the page are unaffected.")


if __name__ == "__main__":
    main()
