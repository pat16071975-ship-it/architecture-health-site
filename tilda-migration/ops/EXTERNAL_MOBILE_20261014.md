# External links and mobile directions/statistics

Owner approval, 14 October 2026: open review links and all external resources
in a new window; fix mobile direction cards on the homepage and statistics
dividers on the homepage and About page.

## Changes

- All 79 HTML pages were scanned. There are 106 external HTTP(S) anchors;
  81 already have `_blank` and opener protection. The remaining 25 anchors
  on seven pages now have `target="_blank" rel="noopener"`.
- Pages: homepage, Contacts, About, Reviews, Laboratory, Cookie policy,
  and personal-data policy. Destinations and visible legal text are unchanged.
- Five existing VK/Telegram shape anchors on homepage, Contacts and About
  also receive narrowly scoped `pointer-events:auto`, so they can be clicked.
  The four review anchors retain the accepted PR100/101 interaction fixes.
- Internal navigation, fragments, `tel:` and other non-HTTP(S) schemes are
  untouched. There is no JavaScript dependency for the new-window behavior.
  Browsers decide whether the new browsing context is a tab or a window.
- Homepage directions use fluid two-column geometry below 640px. Existing
  card order at each breakpoint is retained. Below 360px icons sit above
  the labels to fit the longest word without shrinking the font. At wider
  mobile sizes the icons remain beside the labels. Card heights follow text.
- Below 480px the surplus middle divider from the old four-column statistics
  layout is hidden on homepage and About. The two correctly positioned
  dividers remain. Numbers, labels, fonts, photos and desktop geometry remain.

Future externally directed anchors should also use `_blank` with `noopener`.
Embedded third-party widgets are not rewritten; this package controls links
in this site's own HTML, not a third-party iframe document.

## Controlled installation

Parent: PR101, `f44c1d90187f6cffec4f64353b13d7099d68e399`.

Installer: `tools/apply_external_mobile_20261014.py`.
Manifest: `ops/external_mobile_20261014_manifest.json`.

Seven target HTML files plus 83 protected resources from accepted PR101;
four earlier photos are additionally compared before/after. Fixed server
`az-server`, root user, public root and shared lock. Exact input/output hashes,
backup under `/var/backups/azmednsk/external-mobile-*`, atomic writes with
permissions/ownership retained, exact HTTPS bytes, rollback on failure,
and repeat without writes. Never deploy the whole public directory.

Replacement entries carry an expected occurrence count, so identical anchors
in policy documents are updated completely and unexpected counts stop safely.
Git and live pages receive identical narrow replacements, preserving historical
differences between Git and the deployed site.

## Verification

Isolated PASS: all 106 external anchors, unchanged destinations/internal links,
preserved body after reverting external-link attributes, exact Git/live deltas,
CSS scope and unchanged fonts, installation, repeat, protected resources,
rollback of all seven pages after simulated HTTPS failure, source-drift guards,
and rejection of invalid replacement counts. Photo fixtures in the isolated
rehearsal only test preservation; they are not visual photo evidence.

The package is not deployed yet. After owner installation, verify public hashes,
new-window attributes and a real external-link opening without submitting forms,
the four review links on desktop/mobile, direction cards at 320/390/430px,
statistics on both pages, and unchanged desktop geometry. Existing tablet and
other audit findings are outside this package.
