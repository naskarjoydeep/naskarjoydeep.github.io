#!/usr/bin/env python3
"""
arXiv Digest — daily feed generator. No AI, no API key required.

Pulls new arXiv submissions and publishes each one with its own abstract —
title, authors, categories, link, and the abstract text exactly as the authors
wrote it. No summarization, no critique, no external API of any kind: just
arXiv's free public API plus local matching.

Two filters, applied by field:
  * High-energy theory (HEP_CATEGORIES) — kept on KEYWORD match, and sorted to
    the top. A watched author lifts a paper to the very top tier.
  * Adjacent fields (OTHER_CATEGORIES) — too large and too noisy to keyword-
    filter, so papers are kept ONLY if a watched author is on them.

Run manually with:  python scripts/arxiv_digest.py
Requires:            nothing but the packages in requirements.txt.
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# --------------------------------------------------------------------------
# CONFIGURATION — edit freely, no need to touch anything below this block.
# --------------------------------------------------------------------------

# Core fields: kept on KEYWORD match, and sorted to the top of the page.
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

# Adjacent fields: papers here are kept ONLY if a watched author is on them.
# Keywords are ignored entirely — these categories are far too large for them.
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

# A high-energy paper is kept if its title+abstract contains any of these
# (case-insensitive, accent- and dash-normalized). Grouped by theme purely for
# readability — edit any group.
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

# Authors worth reading whatever they write. In HEP_CATEGORIES a match lifts a
# paper to the top tier; in OTHER_CATEGORIES a match is the *only* way in.
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

# How many days of arXiv submissions to scan on each run (>=2 covers weekends
# and any arXiv processing lag; already-seen IDs are skipped automatically).
DAYS_BACK = 3

# How many days of digest history to keep on the page.
KEEP_DAYS = 30

# Paging for the arXiv API. Big categories like cs.LG post hundreds of papers a
# day, so a single 300-result request would silently truncate.
PAGE_SIZE = 300
MAX_PAGES = 12  # hard cap: 3600 papers per category per run

# Contact info arXiv asks API users to include. Fill in your own email.
USER_AGENT = "arxiv-digest-personal-feed/1.0 (contact: naskar.j@northeastern.edu)"

# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "data", "arxiv-digest.json")
HTML_PATH = os.path.join(REPO_ROOT, "arxiv-digest.html")
ARXIV_API = "http://export.arxiv.org/api/query"


def normalize(s):
    """Lowercase, flatten en/em dashes, and strip accents."""
    s = s.lower().replace("\u2013", "-").replace("\u2014", "-")
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
        time.sleep(3)  # be polite to arXiv's API between categories

    new_entries = []
    for arxiv_id, data in candidates.items():
        if arxiv_id in existing_ids:
            continue
        entry = data["entry"]
        title = entry.title.replace("\n", " ").strip()
        abstract = entry.summary.replace("\n", " ").strip()
        cats = sorted(data["categories"])
        authors = [a.name for a in getattr(entry, "authors", [])] or ["(authors unavailable)"]

        matched = matches_interests(title, abstract, KEYWORDS)
        author_hits = matches_authors(authors, AUTHORS)
        in_hep = any(c in HEP_CATEGORIES for c in cats)

        if in_hep:
            if not (matched or author_hits):
                continue
            priority = 1 if author_hits else 2
        else:
            if not author_hits:
                continue  # adjacent fields: authors only, keywords ignored
            priority = 3

        new_entries.append({
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "categories": cats,
            "published": entry.published[:10],
            "link": f"https://arxiv.org/abs/{arxiv_id}",
            "summary": abstract,
            "matched_terms": matched,
            "matched_authors": author_hits,
            "priority": priority,
        })

    combined = prune_old(existing + new_entries, KEEP_DAYS)
    # Stable sort twice: date descending within each priority tier.
    combined.sort(key=lambda e: e.get("published", ""), reverse=True)
    combined.sort(key=lambda e: e.get("priority", 2))

    save_data(combined)
    update_html(combined)

    tiers = {1: 0, 2: 0, 3: 0}
    for e in new_entries:
        tiers[e["priority"]] += 1
    print(f"Done. {len(new_entries)} new entr{'y' if len(new_entries)==1 else 'ies'} added "
          f"({tiers[1]} hep+author, {tiers[2]} hep+keyword, {tiers[3]} other+author), "
          f"{len(combined)} total on the page.")


if __name__ == "__main__":
    main()
