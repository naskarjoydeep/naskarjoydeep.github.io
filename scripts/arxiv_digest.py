#!/usr/bin/env python3
"""
arXiv Digest — a weekly issue of the 50 new papers closest to my own work.
No AI, no API key required.

Every Friday this pulls the past week of arXiv submissions and ranks them by how
close they are to my research journey: the titles and abstracts of my own papers
(from publications.html) and the descriptions of the rows of research-web.html.
The ranking is plain text similarity (TF-IDF over words and word pairs), plus a
small boost for papers by my collaborators or watched authors. The top ISSUE_SIZE
papers become that week's issue, each shown with its own abstract and the paper of
mine it is closest to.

Candidates:
  * High-energy theory (HEP_CATEGORIES) — every new paper is ranked.
  * Adjacent fields (OTHER_CATEGORIES) — far too large to rank wholesale, so a
    paper is ranked only if it matches a keyword or a watched author.

Run manually with:  python scripts/arxiv_digest.py
Requires:            nothing but the packages in requirements.txt.
"""

import json
import math
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# --------------------------------------------------------------------------
# CONFIGURATION — edit freely, no need to touch anything below this block.
# --------------------------------------------------------------------------

# Core fields: every new paper here is ranked.
# See https://arxiv.org/category_taxonomy
HEP_CATEGORIES = [
    "hep-th",          # holography, gauge theory, Seiberg-Witten, string theory
    "gr-qc",           # quantum gravity, de Sitter holography
    "quant-ph",        # entanglement, quantum error correction, quantum info
    "math-ph",         # spectral curves, operator-algebraic methods
    "math.OA",         # von Neumann algebras, subfactors, crossed products
    "math.QA",         # q-deformation, quantum groups
    "cond-mat.str-el", # topological order, Levin-Wen / string-net models
]

# Adjacent fields: a paper here is ranked only if it matches a keyword or a
# watched author. These categories are far too large to rank wholesale.
OTHER_CATEGORIES = [
    "cs.LG",              # diffusion models, model collapse, NNFT, PINNs
    "stat.ML",            # neural scaling laws, generalization theory
    "cond-mat.dis-nn",    # replica methods, spin glasses, network dynamics
    "cond-mat.stat-mech", # Fokker-Planck, score matching, MIPT, Ising
    "math.AG",            # Gromov-Witten, moduli of curves, topological recursion
    "math.CO",            # partial cubes, hypercube embeddings, matroids
    "physics.flu-dyn",    # turbulence, Navier-Stokes
    # "cs.AI", "cs.CL",   # agents/LLMs — huge; enable only if you want them
]

CATEGORIES = HEP_CATEGORIES + OTHER_CATEGORIES

# Topic phrases (case-insensitive, accent- and dash-normalized). They let papers
# from the adjacent fields into the ranking, give a small ranking boost, and are
# shown as tags. Grouped by theme purely for readability — edit any group.
KEYWORDS = [
    # --- holographic entanglement: core surfaces & wedges ---------------------
    "holographic entanglement", "entanglement entropy", "entanglement wedge",
    "entropy inequalit", "entanglement of purification", "multipartite entanglement",
    "holographic code", "quantum error correcting code", "quantum error correction",
    "AdS/CFT", "bulk reconstruction", "tripartite information",
    "topological entanglement entropy", "contraction map", "holographic entropy cone",
    "Ryu-Takayanagi", "HRT", "extremal surface", "quantum extremal surface",
    "generalized entropy", "subregion duality", "island", "Page curve",
    "python's lunch", "surface/state", "entanglement wedge cross",
    "holographic map", "JLMS", "relative entropy", "modular Hamiltonian",

    # --- multipartite: multi-entropy, reflected entropy, Markov gaps ----------
    "multi-entropy", "multientropy", "reflected entropy", "Markov gap",
    "multi-invariant", "genuine multipartite", "canonical purification",
    "entanglement of assistance", "multiway cut", "discord",
    "mutual information", "subadditivity", "monogamy", "superadditivity",
    "entanglement measure", "entanglement negativity", "entanglement spectrum",
    "entanglement contour",

    # --- entropy cone: bit threads + combinatorial / graph machinery ----------
    "bit thread", "max-flow", "min-cut", "multiflow", "cooperative flow",
    "entropy vector", "extreme ray", "facet", "polytope", "matroid",
    "hypergraph", "graph model", "hypercube", "partial cube", "median graph",
    "isometric embedding", "entropohedron", "stabilizer entropy cone",

    # --- replicas, Renyi, pseudo/timelike entropy -----------------------------
    "Renyi", "replica trick", "replica symmetry breaking",
    "cosmic brane", "pseudo entropy", "pseudoentropy", "pseudo-entropy",
    "timelike entanglement", "time-like entanglement", "temporal entanglement",
    "entanglement in time", "non-hermitian density matrix",

    # --- black holes: interiors, information, wormholes, closed universes -----
    "black hole interior", "information paradox", "Hawking radiation",
    "microstate", "non-isometric", "firewall", "ER=EPR", "ER = EPR",
    "wormhole", "traversable", "baby universe", "closed universe",
    "black hole thermodynamics", "BTZ", "swampland", "weak gravity conjecture",
    "holography of information", "observer complementarity", "split property",
    "scrambling", "quantum chaos", "out-of-time-order", "pole-skipping",
    "eigenstate thermalization", "spectral form factor",

    # --- de Sitter / SYK / JT / random matrices -------------------------------
    "de Sitter", "dS/CFT", "static patch", "cosmological horizon",
    "dSSYK", "Sachdev-Ye-Kitaev", "SYK", "JT gravity", "Jackiw-Teitelboim",
    "Schwarzian", "double-scaled", "double scaled",
    "random matrix", "random tensor", "tensor network", "ensemble average",
    "gravity/ensemble", "large N limit", "large-N", "planar limit", "'t Hooft",

    # --- topological recursion / WP volumes / matrix & minimal strings --------
    "Weil-Petersson", "topological recursion", "Eynard-Orantin", "spectral curve",
    "q-deformed", "matrix model", "moduli space of curves",
    "Mirzakhani", "intersection number", "Gromov-Witten", "BKMP",
    "Virasoro minimal string", "minimal string", "Liouville", "string equation",
    "Fredholm determinant", "Calabi-Yau", "integrable system", "quantum group",

    # --- 3d gravity, Virasoro, bootstrap, BCFT --------------------------------
    "3d gravity", "three-dimensional gravity", "AdS3", "Virasoro",
    "conformal bootstrap", "crossing symmetry", "OPE coefficient",
    "OPE statistics", "large central charge", "large-c", "modular invariance",
    "BCFT", "boundary conformal field theory", "end-of-the-world brane",
    "g-theorem", "defect CFT", "conformal defect",

    # --- operator algebras / modular theory -----------------------------------
    "von Neumann algebra", "type III factor", "type II factor", "subfactor",
    "crossed product", "modular flow", "Tomita-Takesaki", "operator algebra",
    "Yang-Mills", "Seiberg-Witten", "class S theory",
    "algebraic quantum field theory", "Jones index", "Reeh-Schlieder",
    "half-sided modular", "superselection sector", "gravitational dressing",

    # --- topological order, TQFT, anyons, generalized symmetries --------------
    "Levin-Wen", "topological order", "string-net",
    "anyons", "anyonic", "Chern-Simons", "Turaev-Viro", "fusion category",
    "modular tensor category", "topological quantum field theory", "TQFT",
    "non-invertible symmetr", "generalized symmetr", "categorical symmetr",
    "SymTFT", "duality defect", "Kramers-Wannier", "fracton",

    # --- quantum codes, magic, pseudorandomness, monitored dynamics -----------
    "LDPC", "stabilizer code", "toric code", "surface code", "logical qubit",
    "quantum memory", "fault-toleran", "error mitigation", "Clifford",
    "magic state", "nonstabilizerness", "non-stabilizerness", "stabilizer Renyi",
    "pseudorandom", "pseudoentanglement", "pseudo-entanglement",
    "measurement-induced", "monitored", "quantum circuit", "teleportation",
    "decoherence", "open quantum system", "Lindblad", "quantum algorithm",

    # --- RG, c-theorems, scale vs conformal -----------------------------------
    "conformal invariance", "scale invariance", "conformal field theory",
    "renormalization group", "RG flow", "Wilsonian", "c-theorem", "a-theorem",
    "F-theorem", "holographic RG",

    # --- machine learning: diffusion, NNFT, PINNs, LLMs -----------------------
    "neural network", "machine learning", "deep learning", "reinforcement learning",
    "diffusion model", "denoising", "score-based", "score matching",
    "flow matching", "generative model", "model collapse", "self-consuming",
    "classifier-free guidance", "normalizing flow", "optimal transport",
    "Fokker-Planck", "Langevin", "neural scaling law", "scaling law",
    "physics-informed", "neural operator", "Gaussian process",
    "transformer", "large language model", "in-context learning",
    "neural network field theory", "synaptic field theory", "sign problem",

    # --- fluids, turbulence, hydrodynamics ------------------------------------
    "turbulence", "Navier-Stokes", "hydrodynamic", "fluid-gravity", "fluid/gravity",

    # --- amplitudes, Feynman integrals, flat-space & celestial ----------------
    "Feynman integral", "master integral", "scattering amplitude", "amplituhedron",
    "conformal collider", "light-ray", "celestial", "Carrollian",
    "flat space holography", "asymptotic symmetr", "knot invariant",
]

# Authors worth reading whatever they write. A match lets a paper from the
# adjacent fields into the ranking and gives a small ranking boost.
#
# Matching is per-author-name, accent-insensitive, on whole words: every token
# must appear in one author's name. Use a bare surname when it's distinctive
# (that catches "B. Eynard" as well as "Bertrand Eynard"); use the full name
# when the surname is common enough to collide.
AUTHORS = [
    # --- high-energy theory: holography, entanglement, black holes ------------
    "Maldacena", "Witten", "Harlow", "Hartman", "Takayanagi", "Jafferis",
    "Shenker", "Douglas Stanford", "Phil Saad", "Penington", "Xi Dong",
    "Yoshida", "Ning Bao", "Bousso", "Balasubramanian", "Suvrat Raju",
    "Casini", "Huerta", "Almheiri", "Engelhardt", "Aron Wall", "Faulkner",
    "Chris Akers", "Pratik Rath", "Ooguri", "Dabholkar", "Kitaev",
    "Hong Liu", "Zhengwei Liu", "Ronak Soni", "Sridip Pal", "Yikun Jiang",
    "Yangrui Hu", "Guevara", "Simon Ross", "Monica Jinwoo Kang", "Iizuka",
    "Ferko", "Indranil Halder",

    # --- entropy cone / entropy inequalities ---------------------------------
    "Headrick", "Hubeny", "Rangamani", "Rota", "Hernandez-Cuenca", "Czech",

    # --- 3d gravity, minimal strings, matrix models ---------------------------
    "Collier", "Eberhardt", "Clifford Johnson", "Kolanowski", "Saraswat",
    "Okuyama",

    # --- topological recursion / moduli (math.AG, math-ph) --------------------
    "Eynard", "Orantin", "Norbury", "Norman Do", "Bouchard", "Alexandrov",

    # --- graph theory / combinatorics (math.CO) ------------------------------
    "Eppstein",

    # --- diffusion, generative models, model collapse (cs.LG, stat.ML) -------
    "Ganguli", "Sohl-Dickstein", "Yang Song", "Ermon", "Karras", "Samuli Laine",
    "Timo Aila", "Jonathan Ho", "Salimans", "Haewon Jeong", "Yao Qin",
    "Quentin Bertrand", "Gidel", "Richard E. Turner", "Venkataramanan",
    "Borkar",

    # --- learning theory / statistical mechanics of learning -----------------
    "Pehlevan", "Bordelon", "Atanasov", "Daniel A. Roberts", "James Sully",
    "Alexander Maloney", "Yuhai Tu", "Ringel",

    # --- physics-of-ML from the hep-th side ----------------------------------
    "Anindita Maiti", "Jessica N. Howard", "Marc S. Klinger", "Ro Jefferson",
    "Nabil Iqbal", "Hashimoto", "Michael R. Douglas", "Yang-Hui He",
    "Jejjala", "Edward Hirst", "Challenger Mishra", "de Mello Koch",
    "Brandon Robinson", "Hye-Sung Lee", "Donghee Lee", "Yaron Oz",
    "Romuald Janik",

    # --- fluids & turbulence (physics.flu-dyn) -------------------------------
    "Stinis", "Perdikaris", "Siddhartha Mishra", "Yarom", "David Tong",

    # --- codes & quantum matter (cond-mat.dis-nn, quant-ph) ------------------
    "Khemani", "Rakovszky", "Yaodong Li", "Murphy Yuezhen Niu",
]

# Each issue covers the past week of submissions (8 days, so nothing falls
# between two Friday runs; papers already in an earlier issue are skipped).
DAYS_BACK = 8

# How many papers go into each weekly issue, and how many issues stay on the page.
ISSUE_SIZE = 50
KEEP_ISSUES = 8

# Papers with any of these surnames among the authors are left out (my own work).
EXCLUDE_AUTHORS = ["Naskar"]

# Topics I work on that have no paper yet, so the ranking still finds them.
# Each is compared against new papers like one of my own abstracts.
ONGOING_INTERESTS = {
    "Model collapse (ongoing)":
        "model collapse of generative models trained recursively on data generated by earlier models; "
        "self-consuming training loops on synthetic data; loss of the tails and diversity of the distribution",
    "Quantum information bootstrap (ongoing)":
        "quantum information bootstrap; entanglement bootstrap; bootstrapping physical theories from "
        "quantum information principles; bootstrap constraints from entanglement and positivity",
}

# Paging for the arXiv API. cs.LG posts about 5000 papers a week, so a single
# request would silently truncate.
PAGE_SIZE = 300
MAX_PAGES = 25  # hard cap: 7500 papers per category per run

# arXiv asks API users to identify themselves; this points to the site.
USER_AGENT = "naskarjoydeep.github.io arXiv digest (+https://naskarjoydeep.github.io)"

# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "data", "arxiv-digest.json")
HTML_PATH = os.path.join(REPO_ROOT, "arxiv-digest.html")
PUBS_PATH = os.path.join(REPO_ROOT, "publications.html")
WEB_PATH = os.path.join(REPO_ROOT, "research-web.html")
ARXIV_API = "http://export.arxiv.org/api/query"


def normalize(s):
    """Lowercase, flatten en/em dashes, and strip accents."""
    s = s.lower().replace("–", "-").replace("—", "-")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def matches_interests(title, abstract, keywords):
    text = normalize(title + " " + abstract)
    return [kw for kw in keywords if normalize(kw) in text]


def matches_authors(authors, watchlist):
    """Return watchlist entries whose every token appears in one author's name."""
    normed = [normalize(a) for a in authors]
    hits = []
    for watched in watchlist:
        pats = [re.compile(r"\b" + re.escape(t) + r"\b") for t in normalize(watched).split()]
        if any(all(p.search(a) for p in pats) for a in normed):
            hits.append(watched)
    return hits


def fetch_recent(category, days_back, session):
    """Fetch recent papers in a category, newest first, paging until past the cutoff."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    results = []
    for page in range(MAX_PAGES):
        params = {
            "search_query": f"cat:{category}",
            "start": page * PAGE_SIZE,
            "max_results": PAGE_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        r = session.get(ARXIV_API, params=params, timeout=30)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        if not feed.entries:
            return results  # ran out of papers entirely
        for entry in feed.entries:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if published < cutoff:
                return results  # descending order: everything after is older too
            results.append(entry)
        time.sleep(3)  # be polite to arXiv's API between pages
    print(f"Warning: hit the {MAX_PAGES}-page cap on {category}; some papers may be missed.")
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


# ---------------------------------------------------------------- ranking ---

STOPWORDS = set("""
a about above after again against all also am among an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers herself him himself his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other our ours ourselves out over own same she should so some such than that the their
theirs them themselves then there these they this those through to too under until up very was we were what when
where which while who whom why will with would you your yours yourself yourselves
paper papers show shows shown study studies studied propose proposed present presents work results result
new novel approach method methods using use used based via one two three first second find found provide provides
given give gives obtain obtained derive derived well however thus therefore within without may might
various several many much non particular general case cases different further recently known
whether efficient efficiently explain explains require requires required data changes change experimental
experiment experiments framework analysis analyze problem problems property properties important significant
recent understanding understand key including include includes demonstrate demonstrates allows allow enables
enable significantly better performance large small high low able possible approaches techniques
""".split())


def tokens(text):
    """Words of 3+ letters plus adjacent word pairs, with LaTeX and stopwords stripped."""
    text = normalize(re.sub(r"\$[^$]*\$|\\[a-zA-Z]+", " ", text or ""))
    words = [w.strip("-") for w in re.findall(r"[a-z][a-z\-]{2,}", text)]
    words = [w for w in words if w and w not in STOPWORDS]
    return words + [a + " " + b for a, b in zip(words, words[1:])]


def tfidf(docs):
    """Sparse unit vectors (dict term -> weight) for a list of token lists."""
    df = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = {t: (1 + math.log(c)) * math.log((1 + n) / (1 + df[t])) for t, c in tf.items()}
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        vecs.append({t: w / norm for t, w in v.items()})
    return vecs


def cosine(u, v):
    if len(u) > len(v):
        u, v = v, u
    return sum(w * v.get(t, 0.0) for t, w in u.items())


def json_block(path, block_id):
    """The JSON inside <script type="application/json" id=block_id> of a page, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return None
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % block_id, html, re.S)
    return json.loads(m.group(1)) if m else None


def research_profile():
    """My papers and journey rows as documents to compare against, plus my collaborators."""
    pubs = json_block(PUBS_PATH, "publications-data") or {}
    web = json_block(WEB_PATH, "connections-data") or {}
    clusters = {c["id"]: c for c in web.get("clusters", [])}
    nodes = {n["key"]: n for n in web.get("nodes", [])}

    def row_of(key):
        return (clusters.get((nodes.get(key) or {}).get("cluster")) or {}).get("name", "")

    docs, seen = [], set()
    for r in pubs.get("records", []):
        key = r.get("arxiv")
        docs.append({"key": key, "label": r.get("title", ""), "row": row_of(key),
                     "text": (r.get("title", "") + " ") * 2 + (r.get("abstract") or "")})
        seen.add(key)
    # papers in the journey that the INSPIRE sync has not listed yet
    for key, n in nodes.items():
        extra = n.get("extra") or {}
        if key in seen or n.get("kind") == "other" or not extra.get("title"):
            continue
        arxiv = key if re.match(r"^\d{4}\.\d{4,5}$", key) else None
        docs.append({"key": arxiv, "label": extra["title"], "row": row_of(key),
                     "text": (extra["title"] + " ") * 2})
    # each journey row's description, so a row is represented even by one paper
    for c in clusters.values():
        if c.get("name") and c.get("blurb"):
            docs.append({"key": None, "label": c["name"], "row": c["name"], "text": c["blurb"]})
    for label, text in ONGOING_INTERESTS.items():
        docs.append({"key": None, "label": label, "row": "", "text": text, "ongoing": True})

    # full names from the journey's "people" list: initials alone ("J. Tian") collide too often
    collaborators = set()
    for person in (web.get("people") or {}).values():
        parts = normalize(person.get("name", "")).replace(".", " ").split()
        if len(parts) >= 2 and len(parts[0]) > 1:
            collaborators.add((parts[0], parts[-1]))
    return docs, collaborators


def is_collaborator(authors, collaborators):
    for a in authors:
        parts = normalize(a).replace(".", " ").split()
        if len(parts) >= 2 and (parts[0], parts[-1]) in collaborators:
            return True
    return False


def last_friday(today):
    return today - timedelta(days=(today.weekday() - 4) % 7)


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    existing = [e for e in load_existing_data() if e.get("id") != "example" and e.get("issue")]
    issue = last_friday(datetime.now(timezone.utc).date()).isoformat()
    existing = [e for e in existing if e["issue"] != issue]  # re-running a week replaces it
    already = {e["id"].split("v")[0] for e in existing}

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
        time.sleep(3)  # be polite to arXiv's API between categories

    pool = []
    for arxiv_id, data in candidates.items():
        if arxiv_id.split("v")[0] in already:
            continue
        entry = data["entry"]
        title = " ".join(entry.title.split())
        abstract = " ".join(entry.summary.split())
        cats = sorted(data["categories"])
        authors = [a.name for a in getattr(entry, "authors", [])] or ["(authors unavailable)"]
        if any(normalize(x) in normalize(" ".join(authors)) for x in EXCLUDE_AUTHORS):
            continue
        matched = matches_interests(title, abstract, KEYWORDS)
        author_hits = matches_authors(authors, AUTHORS)
        if not any(c in HEP_CATEGORIES for c in cats) and not (matched or author_hits):
            continue  # adjacent fields: only papers that match something are ranked
        pool.append({"id": arxiv_id, "title": title, "authors": authors, "categories": cats,
                     "published": entry.published[:10], "link": f"https://arxiv.org/abs/{arxiv_id}",
                     "summary": abstract, "matched_terms": matched, "matched_authors": author_hits})

    profile, collaborators = research_profile()
    if not profile:
        raise SystemExit("No research profile found (publications.html / research-web.html).")
    texts = [d["text"] for d in profile] + [p["title"] + " " + p["title"] + " " + p["summary"] for p in pool]
    vecs = tfidf([tokens(t) for t in texts])
    pvecs, cvecs = vecs[:len(profile)], vecs[len(profile):]

    for p, v in zip(pool, cvecs):
        sims = sorted(((cosine(v, pv), i) for i, pv in enumerate(pvecs)), reverse=True)
        top = [s for s, _ in sims[:3]]
        similarity = 0.7 * top[0] + 0.3 * sum(top) / len(top)
        # boosts multiply the similarity, so they reorder related papers but cannot lift unrelated ones
        boost = 1 + 0.15 * min(len(p["matched_terms"]), 3) / 3
        if is_collaborator(p["authors"], collaborators):
            boost += 0.5
            p["collaborator"] = True
        elif p["matched_authors"]:
            boost += 0.25
        if not any(c in HEP_CATEGORIES for c in p["categories"]):
            boost *= 0.85  # adjacent fields compete on a slightly higher bar
        score = similarity * boost
        # name the connection: an ongoing interest if that is the best match, otherwise my closest paper
        best = profile[sims[0][1]]
        if not best.get("ongoing"):
            best = next((profile[i] for s, i in sims if profile[i]["key"]), best)
        p["score"] = round(score, 4)
        p["closest"] = {"label": best["label"], "row": best["row"],
                        "link": f"https://arxiv.org/abs/{best['key']}" if best["key"] else None}

    pool.sort(key=lambda p: p["score"], reverse=True)
    picked = pool[:ISSUE_SIZE]
    for rank, p in enumerate(picked, 1):
        p["issue"] = issue
        p["rank"] = rank

    issues = sorted({e["issue"] for e in existing} | {issue}, reverse=True)[:KEEP_ISSUES]
    combined = [e for e in existing if e["issue"] in issues] + picked
    combined.sort(key=lambda e: (e["issue"], -e.get("rank", 999)), reverse=True)

    save_data(combined)
    update_html(combined)
    print(f"Done. Issue {issue}: ranked {len(pool)} candidates, kept the top {len(picked)}. "
          f"{len(issues)} issue(s) on the page.")


if __name__ == "__main__":
    main()
