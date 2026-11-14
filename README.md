# naskarjoydeep.github.io

Personal academic website of Joydeep Naskar, served by GitHub Pages at
**https://naskarjoydeep.github.io**. Plain HTML, CSS and JavaScript: there is no
build step, so every file in the repository is served as it is.

Four GitHub Actions keep the site current: publications are synced from
INSPIRE-HEP, news items for talks and papers are added automatically, an arXiv
digest is published every Friday, and new papers are proposed for the research
journey.

## Pages

| Page | File | Content |
|---|---|---|
| Home | `index.html` | Introduction and links, plus previews of recent news, papers, photos and blog posts. The previews are read from the other pages when the home page loads. |
| Holography, Quantum information, Machine learning, Mathematical physics | `holography.html`, `quantum-information.html`, `machine-learning.html`, `mathematical-physics.html` | A summary of the research in each area, with the relevant papers. Ongoing work sits at the top in an amber box. |
| Publications | `publications.html` | All papers, synced weekly from INSPIRE-HEP, with a link to the research journey. |
| My Research Journey | `research-web.html` | A timeline linking the papers by research area, a world map of where the work was done, the affiliations timeline, and each area's papers in order. |
| arXiv Digest | `arxiv-digest.html` | Every Friday, the 50 new arXiv papers closest to my research. |
| News | `news.html` | Talks, papers and other updates, plus an Instagram section. |
| Blog | `blog.html`, `blogs/*.html` | Articles about my papers, written for physics students. |
| Biography, Talks, Recognition, Teaching, Photography, Contact | `biography.html`, `participation.html`, `recognition.html`, `teaching.html`, `photography.html`, `contact.html` | Hand-written pages. |

## What updates itself

| Workflow | When | What it does | Writes |
|---|---|---|---|
| Publications (`publications.yml`) | Mondays 06:00 UTC | Pulls my papers from INSPIRE-HEP (author record 2044957). | the `publications-data` block in `publications.html` |
| News (`news.yml`) | Mondays 07:00 UTC | Adds seminars and new papers from INSPIRE as news items. | the `news-auto` block in `news.html` |
| Research web connections (`connections.yml`) | After each Publications run | For new or revised papers, proposes links, a row and affiliations for the research journey, and opens a pull request for review (or an issue, if Actions may not open pull requests). | a proposal branch, `connections/proposal` |
| arXiv Digest (`arxiv-digest.yml`) | Fridays 10:00 UTC | Ranks the past week's arXiv papers by text similarity to my papers and keeps the top 50; the last 8 issues stay on the page. | `arxiv-digest.html`, `data/arxiv-digest.json` |

Each workflow can also be started by hand from the **Actions** tab. Research web
connections takes an optional arXiv ID: with one, it does a dry run for that
paper and shows the proposal in the run summary without committing anything.

## Where the data lives

Several pages keep their data in a JSON block inside the page. The scripts
rewrite only their own block, so the rest of a page is safe to edit by hand.

| Block | File | Edited by |
|---|---|---|
| `publications-data` | `publications.html` | the Publications workflow; don't edit by hand |
| `news-auto` | `news.html` | the News workflow; don't edit by hand |
| `news-manual` | `news.html` | hand |
| `digest-data` | `arxiv-digest.html` | the arXiv Digest workflow; don't edit by hand |
| `connections-data` | `research-web.html` | hand, and the proposals from the research web bot |

Paper titles, authors and venues on the research pages and in the research
journey are read from `publications-data` when the page loads, so they update
with the weekly sync. `research-areas.js` fills in the paper lists on the four
research pages.

## How to

**Add a talk.** In `participation.html`, add a `<li>` under the matching topic
title in the Talks section, or start a new topic; topics are ordered by their
most recent talk. Add a matching entry to `news-manual` in `news.html`.

**Add a news item.** Add an object to the `news-manual` block in `news.html`:

```json
{
  "date": "2026-11-09",
  "tag": "Talk",
  "text": "Venue: “Title”.",
  "url": null
}
```

The page sorts by date, so the position in the list doesn't matter. Keep the
JSON valid: commas between entries, none after the last one.

**Add a blog post.** Put the post in `blogs/`, link back to `../blog.html`, and
add an `<li class="post">` at the top of the right year in `blog.html`. Only list
files that exist in `blogs/`.

**Add a paper to a research page.** Write a short summary in the relevant
section and list the paper as

```html
<li data-paper="ARXIV-ID"><a class="paper-title" href="https://arxiv.org/abs/ARXIV-ID">Title</a><span class="paper-meta">arXiv:ARXIV-ID</span></li>
```

`research-areas.js` replaces this with the synced title, authors and venue.
For ongoing work, use `<section class="ongoing">` with
`<span class="ongoing-badge">Ongoing</span>` above the heading, and place it
first on the page.

**Add a paper to the research journey.** New papers on INSPIRE appear in a "Not
yet placed" row until they are given a place. Either merge the bot's proposal,
or edit `connections-data` in `research-web.html` by hand:

- `nodes`: the paper, its short label, its row (`cluster`) and its date
- `edges`: links between papers (`builds`, `related`, `idea` or `thesis`), each with a one-sentence note
- `affiliations`: each author's institutions as printed on the paper's title page (INSPIRE's affiliations contain errors)
- `institutions` and `sites`: names and coordinates for the world map
- `people`: full names of collaborators
- `self`: my affiliations, with dates and roles, for the timeline

**Add a photo.** Add the image file and copy a `<figure>` block in
`photography.html`; the instructions are in a comment on that page.

**Tune the arXiv digest.** Edit the configuration at the top of
`scripts/arxiv_digest.py`: the categories, `KEYWORDS`, `AUTHORS`,
`ONGOING_INTERESTS` (topics without a paper yet), `ISSUE_SIZE` and `KEEP_ISSUES`.

## One-time settings

- **Let the research web bot open pull requests.** *Settings → Actions → General
  → Workflow permissions → Allow GitHub Actions to create and approve pull
  requests*. Without it, the bot posts its proposal as an issue instead.
- **Contact form.** `contact.html` sends messages through Web3Forms. The access
  key on the page is a public identifier, not a secret; the destination address
  is held by Web3Forms.
- **Instagram on the News page.** See the comments in `news.html` for the two
  ways to show posts.

## Running the scripts locally

```bash
pip install -r scripts/requirements.txt
```

```bash
python scripts/inspire_publications.py
```

The other scripts run the same way: `news_updates.py`, `arxiv_digest.py` and
`classify_connections.py`. Without a `GITHUB_TOKEN`,
`classify_connections.py` skips the model step and lists citations for review
instead. A dry run for one paper:

```bash
FORCE_PAPER=2412.05484 python scripts/classify_connections.py
```

## Repository layout

```
.github/workflows/   the four scheduled workflows
scripts/             the Python scripts they run
data/                arxiv-digest.json
blogs/               blog posts
research-areas.js    fills in paper lists on the research pages
*.html               the pages
```
