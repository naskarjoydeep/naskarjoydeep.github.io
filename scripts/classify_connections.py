#!/usr/bin/env python3
"""
Propose research-web connections for new or revised papers.

Runs after the INSPIRE sync. The script:
  1. Reads the synced records from publications.html and the curated web from the
     connections-data block in research-web.html.
  2. Finds papers that are not in the web yet, plus papers with a new arXiv version.
  3. For each one, gets its references to my own papers from INSPIRE (exact), and the
     sentences around each such citation from the arXiv LaTeX source.
  4. Asks a free LLM (GitHub Models, authenticated with the workflow's GITHUB_TOKEN) to
     label each citation as "builds" / "related" / "drop", to pick a row (cluster) for a
     new paper, and to suggest at most a few uncited shared ideas.
  5. Rewrites the connections-data block and writes connections-report.md, which the
     workflow uses as the body of a pull request for review.

Without GITHUB_TOKEN (e.g. a local run) the LLM step is skipped: every own citation is
proposed as "related" and the row is chosen by citation and coauthor votes.

Run manually with:  python scripts/classify_connections.py
Dry run on a paper already in the web (writes only the report):
                    FORCE_PAPER=2409.17317 python scripts/classify_connections.py
"""

import gzip
import io
import json
import os
import re
import tarfile
import time
from collections import Counter

import feedparser
import requests

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

MODEL = os.environ.get("MODELS_MODEL", "openai/gpt-4o-mini")   # any GitHub Models chat model
MODELS_URL = "https://models.github.ai/inference/chat/completions"
USER_AGENT = "naskarjoydeep.github.io connections bot"
SELF_SURNAME = "naskar"

# Free-tier requests are capped at roughly 8k input tokens, so keep prompts small.
MAX_CONTEXT_CHARS = 700      # per citation context
MAX_CONTEXTS_PER_PAPER = 3   # per cited paper
MAX_PROMPT_CHARS = 18000

# Colours for rows the bot has to create (kept in the site's palette).
NEW_CLUSTER_COLORS = ["#0f766e", "#7c2d12", "#4338ca", "#a21caf", "#3f6212", "#9f1239"]

# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBS_PATH = os.path.join(REPO_ROOT, "publications.html")
WEB_PATH = os.path.join(REPO_ROOT, "research-web.html")
REPORT_PATH = os.path.join(REPO_ROOT, "connections-report.md")

PUBS_RE = re.compile(r'(<script type="application/json" id="publications-data">)(.*?)(</script>)', re.DOTALL)
CONN_RE = re.compile(r'(<script type="application/json" id="connections-data">)(.*?)(</script>)', re.DOTALL)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


# ----------------------------- reading / writing ---------------------------

def read_block(path, pattern):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    m = pattern.search(html)
    if not m:
        raise SystemExit(f"No JSON block matching {pattern.pattern[:60]}… in {path}")
    return html, json.loads(m.group(2))


def write_connections(html, conn):
    blob = json.dumps(conn, indent=2, ensure_ascii=False)
    new_html, count = CONN_RE.subn(lambda mo: mo.group(1) + "\n" + blob + "\n" + mo.group(3), html)
    if count != 1:
        raise RuntimeError("Expected one connections-data block in research-web.html")
    with open(WEB_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)


def record_key(r):
    return r.get("arxiv") or f"inspire:{r.get('inspire_id')}"


# ----------------------------- external data -------------------------------

def arxiv_meta(ids):
    """Latest version number and first-submission date for each arXiv ID."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        url = "https://export.arxiv.org/api/query?max_results=100&id_list=" + ",".join(chunk)
        feed = feedparser.parse(session.get(url, timeout=40).text)
        for e in feed.entries:
            m = re.search(r"abs/([^v]+)v(\d+)$", e.get("id", ""))
            if m:
                out[m.group(1)] = {"version": int(m.group(2)), "date": e.get("published", "")[:10]}
        time.sleep(3)  # arXiv asks for a pause between API calls
    return out


def own_citations(recid, own_by_recid):
    """Keys of my own papers that INSPIRE lists in this record's references."""
    url = f"https://inspirehep.net/api/literature/{recid}?fields=references.record"
    try:
        refs = session.get(url, timeout=40).json()["metadata"].get("references", [])
    except (requests.RequestException, ValueError, KeyError):
        return []
    keys = []
    for ref in refs:
        rid = ref.get("record", {}).get("$ref", "").rsplit("/", 1)[-1]
        if rid.isdigit() and int(rid) in own_by_recid and own_by_recid[int(rid)] not in keys:
            keys.append(own_by_recid[int(rid)])
    return keys


def format_author(full_name):
    """'Bao, Ning' -> 'N. Bao', matching how publications.html lists authors."""
    if "," not in full_name:
        return full_name.strip()
    surname, _, given = full_name.partition(",")
    initials = [part[0].upper() + "." for part in given.replace(".", " ").split() if part]
    return (" ".join(initials) + " " + surname.strip()).strip() if initials else surname.strip()


def new_coauthors(recid, people):
    """People entries (full name, affiliation on this paper) for coauthors not yet known."""
    url = f"https://inspirehep.net/api/literature/{recid}?fields=authors.full_name,authors.affiliations"
    try:
        authors = session.get(url, timeout=40).json()["metadata"].get("authors", [])
    except (requests.RequestException, ValueError, KeyError):
        return {}
    out = {}
    for a in authors:
        full = a.get("full_name", "")
        short = format_author(full)
        if SELF_SURNAME in full.lower() or short in people:
            continue
        surname, _, given = full.partition(",")
        affs = [x.get("value") for x in a.get("affiliations", []) if x.get("value")]
        entry = {"name": (given.strip() + " " + surname.strip()).strip(), "auto": True}
        if affs:
            entry["affiliation"] = affs[0]
        out[short] = entry
    return out


def title_block(tex, limit=4000):
    """The LaTeX between \\begin{document} (or \\title) and the abstract, where authors and affiliations live."""
    start = tex.find("\\title")
    if start < 0:
        start = tex.find("\\begin{document}")
    head = tex[max(start, 0):]
    stop = re.search(r"\\begin\{abstract\}|\\abstract\{|\\maketitle", head)
    head = head[: stop.start()] if stop and stop.start() > 200 else head
    return re.sub(r"(?<!\\)%.*", "", head)[:limit]


def match_institution(aff, institutions):
    """Id of the known institution whose longest match phrase occurs in the affiliation string."""
    low, best = aff.lower(), (0, None)
    for iid, inst in institutions.items():
        for phrase in inst.get("match", []):
            if phrase.lower() in low and len(phrase) > best[0]:
                best = (len(phrase), iid)
    return best[1]


def arxiv_source(arxiv_id):
    """(all .tex text, all .bib/.bbl text) from the arXiv e-print, or ('', '')."""
    try:
        data = session.get(f"https://arxiv.org/e-print/{arxiv_id}", timeout=60).content
    except requests.RequestException:
        return "", ""
    tex, bib = [], []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue
                name = m.name.lower()
                if name.endswith((".tex", ".bib", ".bbl")):
                    text = tar.extractfile(m).read().decode("utf-8", "ignore")
                    (tex if name.endswith(".tex") else bib).append(text)
    except tarfile.TarError:
        try:  # a single gzipped .tex file
            tex.append(gzip.decompress(data).decode("utf-8", "ignore"))
        except OSError:
            return "", ""
    return "\n".join(tex), "\n".join(bib)


def bib_keys_for(paper, bib_text):
    """Citation keys in the .bib/.bbl text that point to the given paper."""
    needles = [n for n in (paper.get("arxiv"), paper.get("doi")) if n]
    title = re.sub(r"[^a-z0-9 ]", "", (paper.get("title") or "").lower())
    title_words = " ".join(title.split()[:6])
    keys = set()
    for m in re.finditer(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", bib_text, re.DOTALL):
        body = m.group(2)
        flat = re.sub(r"[^a-z0-9 ]", "", body.lower())
        if any(n in body for n in needles) or (title_words and title_words in " ".join(flat.split())):
            keys.add(m.group(1))
    for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|\Z)", bib_text, re.DOTALL):
        body = m.group(2)
        flat = " ".join(re.sub(r"[^a-z0-9 ]", "", body.lower()).split())
        if any(n in body for n in needles) or (title_words and title_words in flat):
            keys.add(m.group(1))
    return keys


def citation_contexts(tex, keys):
    """Paragraphs of the LaTeX body that cite any of the given keys."""
    if not keys:
        return []
    body = tex.split("\\begin{document}", 1)[-1]
    body = re.sub(r"(?<!\\)%.*", "", body)
    out = []
    for para in re.split(r"\n\s*\n", body):
        for cm in re.finditer(r"\\cite\w*\*?(?:\[[^\]]*\])*\{([^}]*)\}", para):
            if keys & {k.strip() for k in cm.group(1).split(",")}:
                start = max(0, cm.start() - MAX_CONTEXT_CHARS // 2)
                snippet = " ".join(para[start:start + MAX_CONTEXT_CHARS].split())
                if snippet not in out:
                    out.append(snippet)
                break
        if len(out) >= MAX_CONTEXTS_PER_PAPER:
            break
    return out


def intro_excerpt(tex, limit=3000):
    body = tex.split("\\begin{document}", 1)[-1]
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\\(begin|end)\{[^}]*\}|\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", body)
    return " ".join(body.split())[:limit]


# ----------------------------- LLM -----------------------------------------

def ask_llm(system, user):
    """One JSON-mode chat completion on GitHub Models, or None if unavailable."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    payload = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user[:MAX_PROMPT_CHARS]}],
    }
    for attempt in range(3):
        r = requests.post(MODELS_URL, json=payload, timeout=120,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        if r.status_code == 429:
            time.sleep(int(r.headers.get("retry-after", "30")) + 1)
            continue
        if not r.ok:
            print(f"Model call failed ({r.status_code}): {r.text[:200]}")
            return None
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (KeyError, ValueError, IndexError):
            print("Model returned non-JSON output; skipping.")
            return None
    return None


CLASSIFY_SYSTEM = (
    "You read a physics paper and decide how it uses earlier papers by the same author. "
    "Reply with JSON only."
)

CLASSIFY_PROMPT = """Paper: {title}
Abstract: {abstract}

For each earlier paper below you get the sentences where the paper cites it.
Label each one:
- "builds": the paper uses a result, method, construction or object of the earlier paper, or extends or corrects it.
- "related": the earlier paper is discussed specifically (a contrast, a proposal, a pointer to future work) but not used.
- "drop": it only appears in a list of references or as generic background.

Write "note" as one plain sentence for a reader of a research website, naming what is used or discussed.
Copy a short "evidence" quote (under 20 words) from the context.

Earlier papers:
{items}

Reply as {{"links": [{{"key": "...", "type": "builds|related|drop", "note": "...", "evidence": "..."}}]}}"""

AFFIL_SYSTEM = "You read the title block of a physics paper and list each author's affiliations. Reply with JSON only."

AFFIL_PROMPT = """Authors (use exactly these names as keys): {authors}

Title block (LaTeX):
{block}

Reply as {{"authors": {{"<author>": ["<full affiliation as printed>", ...]}},
"places": [{{"affiliation": "<an affiliation from above>", "name": "<short institution name>", "city": "<city, country>", "lat": <number>, "lon": <number>}}]}}
List every distinct affiliation once in "places" with approximate coordinates of the institution."""

PLACE_SYSTEM = (
    "You place a new physics paper in an author's map of research areas. Be conservative: "
    "suggest a shared idea only if the text names a specific object, method or result that the other paper also uses. "
    "Reply with JSON only."
)

PLACE_PROMPT = """New paper: {title}
Abstract: {abstract}
Opening of the paper: {intro}

Research areas (rows) in the map:
{clusters}

Papers already in the map:
{nodes}

Papers this one already cites: {cited}

Reply as {{"cluster": "<row id, or new>", "new_cluster_name": "<only if new>", "label": "<2-3 word label>",
"ideas": [{{"key": "<existing paper key not already cited>", "note": "<one plain sentence naming the shared object or method>"}}]}}
Give at most 3 ideas, and an empty list if nothing specific is shared."""


# ----------------------------- main ----------------------------------------

def main():
    _, pubs = read_block(PUBS_PATH, PUBS_RE)
    web_html, conn = read_block(WEB_PATH, CONN_RE)
    records = pubs.get("records", [])
    nodes = conn.setdefault("nodes", [])
    edges = conn.setdefault("edges", [])
    clusters = conn.setdefault("clusters", [])

    # index curated nodes under every key a synced record could have
    node_by_key = {}
    for n in nodes:
        node_by_key[n["key"]] = n
        extra = n.get("extra") or {}
        if extra.get("inspire_id"):
            node_by_key[f"inspire:{extra['inspire_id']}"] = n
        if extra.get("doi"):
            node_by_key[f"doi:{extra['doi']}"] = n

    def node_for(r):
        for k in (record_key(r), f"inspire:{r.get('inspire_id')}", f"doi:{r.get('doi')}"):
            if k in node_by_key:
                return node_by_key[k]
        return None

    own_by_recid = {}
    for r in records:
        n = node_for(r)
        if r.get("inspire_id"):
            own_by_recid[r["inspire_id"]] = n["key"] if n else record_key(r)
    for n in nodes:
        rid = (n.get("extra") or {}).get("inspire_id")
        if rid:
            own_by_recid.setdefault(rid, n["key"])

    meta = arxiv_meta([r["arxiv"] for r in records if r.get("arxiv")])

    # targets: papers missing from the web, and papers with a new arXiv version
    targets, bookkeeping = [], False
    for r in records:
        n = node_for(r)
        ax = r.get("arxiv")
        if n is None:
            targets.append(("new", r))
        elif ax and ax in meta:
            seen = n.get("arxiv_version")
            if seen is None:
                n["arxiv_version"] = meta[ax]["version"]   # first run: just remember it
                bookkeeping = True
            elif meta[ax]["version"] > seen:
                targets.append(("revised", r))

    force = os.environ.get("FORCE_PAPER", "").strip()
    if force:
        targets = [("new", r) for r in records if record_key(r) == force]
        if not targets:
            raise SystemExit(f"FORCE_PAPER={force} is not among the synced records.")
        n = node_by_key.get(force)
        if n:  # hide it so it is classified as if it were new
            nodes.remove(n)
            node_by_key = {k: v for k, v in node_by_key.items() if v is not n}

    if not targets:
        if bookkeeping:
            write_connections(web_html, conn)
            print("No new or revised papers; recorded current arXiv versions.")
        else:
            print("No new or revised papers.")
        return

    cluster_ids = {c["id"] for c in clusters}
    key_to_cluster = {n["key"]: n.get("cluster") for n in nodes}
    existing_pairs = {(e["from"], e["to"]) for e in edges}
    report = ["## Proposed research-web connections", "",
              f"Model: `{MODEL}` on GitHub Models. Review each item; edit or delete entries in the "
              "`connections-data` block of `research-web.html` before merging. Entries added by this bot carry `\"auto\": true`.", ""]

    for kind, r in targets:
        key = record_key(r)
        ax = r.get("arxiv")
        header = [f"### {'New' if kind == 'new' else 'Revised'}: {r['title']}",
                  f"`{key}` · {', '.join(r.get('authors', []))}", ""]
        lines = []

        cited = [k for k in own_citations(r.get("inspire_id"), own_by_recid) if k != key and k in node_by_key]
        tex, bib = arxiv_source(ax) if ax else ("", "")

        # 1. label each citation of my own papers
        items, contexts = [], {}
        for ck in cited:
            n = node_by_key.get(ck)
            rec = next((x for x in records if record_key(x) == ck), None)
            paper = {"arxiv": ck if re.match(r"^\d{4}\.\d{4,5}$", ck) else None,
                     "doi": (rec or {}).get("doi") or ((n or {}).get("extra") or {}).get("doi"),
                     "title": (rec or {}).get("title") or ((n or {}).get("extra") or {}).get("title", "")}
            contexts[ck] = citation_contexts(tex, bib_keys_for(paper, bib)) if tex else []
            items.append(f"- key: {ck}\n  title: {paper['title']}\n  contexts: " +
                         (" | ".join(contexts[ck]) if contexts[ck] else "(no citation context found)"))

        labels = {}
        if items:
            res = ask_llm(CLASSIFY_SYSTEM, CLASSIFY_PROMPT.format(
                title=r["title"], abstract=(r.get("abstract") or "")[:1500], items="\n".join(items)))
            for link in (res or {}).get("links", []):
                if link.get("key") in contexts and link.get("type") in ("builds", "related", "drop"):
                    labels[link["key"]] = link

        new_edges = []
        for ck in cited:
            link = labels.get(ck) or {"type": "related", "note": f"Cites {node_by_key[ck].get('label', ck)}.",
                                      "evidence": "" if contexts.get(ck) else "(no citation context found; labelled without the model)"}
            if link["type"] == "drop" or (ck, key) in existing_pairs:
                lines.append(f"- skipped `{ck}` ({link['type'] if link['type'] == 'drop' else 'already linked'})")
                continue
            e = {"from": ck, "to": key, "type": link["type"], "note": link.get("note", ""), "auto": True}
            new_edges.append(e)
            lines.append(f"- **{link['type']}** ← `{ck}` ({node_by_key[ck].get('label', ck)}): {e['note']}"
                          + (f"  \n  > {link['evidence']}" if link.get("evidence") else ""))

        # 2. place a new paper in a row, and look for uncited shared ideas
        if kind == "new":
            votes = Counter()
            for e in new_edges:
                votes[key_to_cluster.get(e["from"])] += 1 if e["type"] == "builds" else 0.5
            coauthors = {a.lower() for a in r.get("authors", []) if SELF_SURNAME not in a.lower()}
            for other in records:
                on = node_for(other)
                if on and coauthors & {a.lower() for a in other.get("authors", [])}:
                    votes[on.get("cluster")] += 0.5
            votes.pop(None, None)
            votes.pop("thesis", None)
            voted = votes.most_common(1)[0][0] if votes else None

            res = ask_llm(PLACE_SYSTEM, PLACE_PROMPT.format(
                title=r["title"], abstract=(r.get("abstract") or "")[:1500], intro=intro_excerpt(tex) if tex else "(not available)",
                clusters="\n".join(f"- {c['id']}: {c.get('name') or c['id']} — {c.get('blurb', '')}" for c in clusters if c.get("name")),
                nodes="\n".join(f"- {n['key']} [{n.get('cluster')}]: {n.get('label')}" for n in nodes),
                cited=", ".join(cited) or "none")) or {}

            cluster = res.get("cluster")
            if cluster == "new" and res.get("new_cluster_name"):
                cid = re.sub(r"[^a-z0-9]+", "-", res["new_cluster_name"].lower()).strip("-")[:24] or "new"
                if cid not in cluster_ids:
                    used = {c.get("color") for c in clusters}
                    color = next((c for c in NEW_CLUSTER_COLORS if c not in used), "#64748b")
                    band = max((c.get("band", 0) for c in clusters), default=-1)
                    clusters.append({"id": cid, "name": res["new_cluster_name"], "color": color,
                                     "band": int(band) + 1, "auto": True})
                    cluster_ids.add(cid)
                cluster = cid
            elif cluster not in cluster_ids or cluster == "thesis":
                cluster = voted if voted in cluster_ids else None

            date = meta.get(ax, {}).get("date") or (r.get("year") or "")
            node = {"key": key, "label": (res.get("label") or r["title"][:28]).strip(), "date": date[:10], "auto": True}
            if cluster:
                node["cluster"] = cluster
            if ax in meta:
                node["arxiv_version"] = meta[ax]["version"]
            nodes.append(node)
            node_by_key[key] = node
            key_to_cluster[key] = cluster
            header.insert(2, f"Row: **{cluster or 'not yet placed'}** (model: {res.get('cluster', 'n/a')}; "
                             f"citation/coauthor vote: {voted or 'none'})")

            for idea in (res.get("ideas") or [])[:3]:
                ik = idea.get("key")
                if ik in key_to_cluster and ik != key and ik not in cited and (ik, key) not in existing_pairs:
                    new_edges.append({"from": ik, "to": key, "type": "idea", "note": idea.get("note", ""), "auto": True})
                    lines.append(f"- **shared idea (suggested)** ↔ `{ik}` ({node_by_key[ik].get('label', ik)}): {idea.get('note', '')}")
            # world map: affiliations as printed on the paper (INSPIRE's are sometimes wrong)
            insts = conn.setdefault("institutions", {})
            short_names = r.get("authors", [])
            res = ask_llm(AFFIL_SYSTEM, AFFIL_PROMPT.format(authors=", ".join(short_names), block=title_block(tex))) if tex else None
            if res and res.get("authors"):
                places = {pl.get("affiliation"): pl for pl in res.get("places", []) if pl.get("affiliation")}
                entry = {}
                for author, affl in res["authors"].items():
                    if author not in short_names:
                        continue
                    ids = []
                    for aff in affl:
                        iid = match_institution(aff, insts)
                        if not iid:
                            pl = places.get(aff, {})
                            iid = re.sub(r"[^a-z0-9]+", "-", (pl.get("name") or aff).lower()).strip("-")[:30]
                            if iid not in insts:
                                insts[iid] = {"name": pl.get("name") or aff, "short": pl.get("name") or aff,
                                              "city": pl.get("city", ""), "lat": pl.get("lat"), "lon": pl.get("lon"),
                                              "site": iid, "match": [pl.get("name", aff).lower()], "auto": True}
                                # map label: the city, so the circle reads like the others
                                conn.setdefault("sites", {})[iid] = {"name": (pl.get("city") or pl.get("name") or aff).split(",")[0],
                                                                     "label": "right", "auto": True}
                                lines.append(f"- **new place** {insts[iid]['name']} ({insts[iid]['city']}; "
                                             f"lat {pl.get('lat')}, lon {pl.get('lon')} from the model: check)")
                        if iid not in ids:
                            ids.append(iid)
                    entry[author] = ids
                conn.setdefault("affiliations", {})[key] = {"authors": entry, "auto": True}
                lines.append("- **affiliations** (check against the PDF title page): " + "; ".join(
                    f"{a} → {', '.join(insts[i]['name'] for i in ids)}" for a, ids in entry.items()))
            else:
                lines.append("- **affiliations** not extracted; add them to `affiliations` by hand from the title page")

            # collaborators frame: new coauthors with their affiliation on this paper
            people = conn.setdefault("people", {})
            for short, entry in new_coauthors(r.get("inspire_id"), people).items():
                people[short] = entry
                lines.append(f"- **new collaborator** {entry['name']} ({entry.get('affiliation', 'affiliation not listed on INSPIRE')})")
        else:
            node_for(r)["arxiv_version"] = meta[ax]["version"]

        edges.extend(new_edges)
        existing_pairs.update((e["from"], e["to"]) for e in new_edges)
        report.extend(header + (lines or ["- no links to my other papers found"]) + [""])

    if force:
        report.insert(0, f"> Dry run for `{force}`: nothing was written to research-web.html.\n")
    else:
        write_connections(web_html, conn)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print(f"Proposed connections for {len(targets)} paper(s); see connections-report.md.")


if __name__ == "__main__":
    main()
