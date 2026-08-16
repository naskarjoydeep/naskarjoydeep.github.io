# Site pages — current set

```
naskarjoydeep.github.io/
├── index.html                  (replace)
├── biography.html              (new — positions + education)
├── photography.html            (new — gallery)
├── publications.html           (auto-synced from INSPIRE)
├── holography.html
├── quantum-information.html
├── machine-learning.html
└── mathematical-physics.html
```

## What changed this round

- **Positions and Education** moved out of `index.html` into `biography.html`.
- **Teaching Assistantship** removed from the live site. It isn't deleted —
  it's commented out at the bottom of `biography.html`, so uncomment it there
  if you change your mind.
- Badge now reads **Publications** (not "Publications & Preprints"). The page
  still separates preprints from published papers internally.
- New badge row: Biography · Publications · Photography · arXiv Digest.

## index.html placeholders

Two commented blocks are waiting for you, both with working CSS already in
the stylesheet:

- `ABOUT PARAGRAPH` — uncomment the `<section id="about">` block and write.
- `PHOTOGRAPHY COLLAGE` — uncomment `<section id="collage">`, point the
  `<img>` tags at your files. The grid reflows for any number of images, so
  add or delete `<img>` lines freely.

Because they're inside HTML comments, they render as nothing until you
uncomment them — the page is clean in the meantime.

## Adding photographs

1. Make a `photos/` folder in the repo and put images there.
2. In `photography.html`, duplicate a `<figure>` block (the pattern is in an
   HTML comment right above the gallery) and change `src`, `alt`, caption.
3. Delete the `.placeholder` div once the first photo is in.

Clicking any photo opens a lightbox; Escape or a click anywhere closes it.
The click handler is delegated, so photos you add later work with no JS edits.

Two practical notes: resize to roughly 1600px on the long edge before
uploading (GitHub's web uploader caps at 25 MB/file, and full-size camera
JPEGs make the page slow), and write real `alt` text — it's what screen
readers announce, and it's what shows if an image fails to load.

## Small fixes carried over

`index.html` previously had an unclosed `<ul>` in the teaching block and a
`&#38` missing its semicolon. Both are gone now — the teaching block itself
is removed, and the remaining lists are balanced.
