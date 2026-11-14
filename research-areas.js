// Fills the paper lists on the research-area pages from the publications-data block of
// publications.html (kept current by the INSPIRE sync). Each <li data-paper="ARXIV-ID">
// already holds a plain arXiv link, so the lists still work if this script cannot load.
(function () {
  'use strict';

  var items = document.querySelectorAll('li[data-paper]');
  if (!items.length || !window.fetch) return;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Titles from INSPIRE can carry LaTeX, e.g. $\mathscr{N}=2$ or $\mathrm{AdS}/\mathrm{CFT}$.
  function plain(title) {
    return String(title || '')
      .replace(/\\mathscr\{N\}/g, '𝒩')
      .replace(/\\math[a-z]+\{([^}]*)\}/g, '$1')
      .replace(/\$/g, '');
  }

  fetch('publications.html', { cache: 'no-cache' })
    .then(function (r) { return r.text(); })
    .then(function (html) {
      var m = /<script type="application\/json" id="publications-data">([\s\S]*?)<\/script>/.exec(html);
      var records = m ? (JSON.parse(m[1]).records || []) : [];
      var byArxiv = {};
      records.forEach(function (r) { if (r.arxiv) byArxiv[r.arxiv] = r; });

      Array.prototype.forEach.call(items, function (li) {
        var id = li.getAttribute('data-paper'), r = byArxiv[id];
        if (!r) return;
        var authors = (r.authors || []).map(function (a) {
          return /naskar/i.test(a) ? '<span class="me">' + esc(a) + '</span>' : esc(a);
        }).join(', ');
        li.innerHTML =
          '<a class="paper-title" href="https://arxiv.org/abs/' + esc(id) + '" target="_blank" rel="noopener">' + esc(plain(r.title)) + '</a>' +
          '<span class="paper-meta">' + authors + ' · ' + (r.journal ? esc(r.journal) : 'Preprint') + ' · ' +
          '<a href="research-web.html#' + esc(id) + '">Research journey</a></span>';
      });
    })
    .catch(function () { /* keep the plain arXiv links */ });
})();
