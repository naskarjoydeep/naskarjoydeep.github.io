# Court Record Search — static web version

A single self-contained `index.html`. No build step, no dependencies, no
backend. It calls the CourtListener API directly from the browser and does the
name disambiguation client-side.

## Deploying to GitHub Pages

1. Commit `docs/index.html` to your repository.
2. **Settings → Pages → Build and deployment**
   - Source: *Deploy from a branch*
   - Branch: `main`, folder: `/docs`
3. It appears at `https://<user>.github.io/<repo>/`.

If you would rather serve from the repository root, move `index.html` up one
level and set the folder to `/ (root)`.

Locally:

```bash
python3 serve.py
```

## How the API token works

GitHub Pages is static hosting, so there is nowhere to keep a secret. A token
baked into the page would be readable by anyone who views source.

So the page never ships a token. Each user pastes **their own**, and it is:

- kept in that browser's `localStorage`,
- sent only to `courtlistener.com`,
- never transmitted anywhere else, because there is no backend to send it to.

This works because CourtListener sets permissive CORS headers and allows the
`Authorization` header from any origin — verified before building:

```
access-control-allow-origin: https://example.github.io
access-control-allow-headers: accept, authorization, content-type, ...
access-control-allow-methods: GET, HEAD, OPTIONS
```

A token is optional. Without one the anonymous quota is small and large
searches will be throttled.

## What it does

Same behaviour as the `courtsearch.py` CLI, ported to JavaScript:

- **Party-field search**, not full text. `party:"Sarah Johnson"` returns 87
  dockets where full text returns 738; the difference is cases where the name
  appears in a brief or an attorney roster rather than as a party.
- **Matched-party display**, including on mass actions. One tested docket had
  498 parties, where showing the first six alphabetically tells you nothing.
- **Name disambiguation**, grouping records that share affirmative evidence and
  leaving everything else explicitly unlinked.
- **Officials filtering.** Title patterns and a seed roster always run. The
  corpus-frequency lookup is opt-in because it costs extra requests.

## Two things worth knowing about the port

**The frequency checkbox does not disable the offline roster.** Title matching
and the seed roster cost nothing and always run; only the frequency lookup needs
the network. An earlier version tied all three to the checkbox, which meant
Wexford Health Sources could link strangers whenever the box was unchecked.

**Feature keys use a named separator.** Keys are `kind<SEP>value`. The join and
the splits once drifted apart — the join used one character and one split used
another — and the officials roster silently stopped running with no error at
all. `const SEP` exists so that cannot recur.

## Limits

- **Not a background check.** It cannot be used for decisions about employment,
  housing, credit or insurance; FCRA routes those through a consumer reporting
  agency with consent and an adverse action notice.
- **A name is not an identifier.** One search returns records belonging to
  several different people.
- **Unlinked means no evidence**, not "different person".
- **Absence of a match is not a clean record.** RECAP is a partial PACER mirror
  and most state trial courts are not indexed at all.
- An official recorded without a title — a warden named simply "David Gomez" —
  is only caught by the frequency lookup, so with that box unchecked they can
  still link unrelated records. Check the `Linked by:` line.

## If you publish this beyond yourself

Going from a page you use to a service others query changes your legal position:

- **FCRA.** Third parties using it for employment, tenancy, credit or insurance
  decisions can put you in consumer-reporting-agency territory. Spokeo settled
  with the FTC for $800K on essentially that theory while disclaiming CRA status.
- **Data broker registration.** California (Delete Act), Vermont, Texas and
  Oregon maintain registries that a public people-search service can fall under.
- **CourtListener is a non-profit** on donated infrastructure. Do not point
  public traffic at it without rate limiting and your own token.

Keeping it private — your own Pages site, your own token — avoids all of this.
