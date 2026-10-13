# Main-page content cleanup — 2026-10-13

The owner approved removing the service label over the clinic image and the
news heading that has no news beneath it. This package removes exactly:

- `<span>Видео клиники · подключение проверяется</span>`
- The complete `section.container.content-section.section-title` containing
  only `Актуальные новости медицинского центра`, including its empty spacing.

Only `public/index.html` changes. The clinic image, mission text, booking
section, image sources, links, metadata, CSS, and the PR94 loading changes stay
unchanged. This does not add a video player or a news feed.

## Installation

Run the pinned `tools/apply_home_cleanup_20261013.py --apply` with sudo on
`az-server`. The intended live root is
`/var/www/azmednsk-test/tilda-migration/public`.

The installer patches the current live HTML by two exact string removals;
it does not copy the repository's public directory. Git HTML and live HTML
have historical differences, so the live manifest guards the live version.

- Before home SHA256: `3673b475c12b911ad4ad23b9d52ab65cfa65cb68bcefa7e0e29c2edcfbca8144`
- After home SHA256: `68954b5de3282236ca3f372dc6a5d3c0f4308a1a1de71c75d6988b58d6eedd50`
- Installer SHA256: `d8c602b6dc13a6a0bcf4a9904eeb812ad17ce9d7439193f6b66b8501aa0facf6`

Preflight rejects an unexpected home version or drift in 88 protected files
from the accepted PR94 inventory. The home was fetched again before preparing
this package and matched that inventory. The protected files are checked on
the server before writing; they were not all downloaded again for this package.
The four earlier approved photo files are additionally compared before/after.

Installation takes the shared deployment lock, creates a dated backup under
`/var/backups/azmednsk/home-cleanup-*`, preserves file owner/mode, and performs
an atomic write. HTTPS must return status 200 and exactly the expected HTML.
An installation/verification failure restores the original home unless a
concurrent edit prevents safe rollback. Repeated installation is idempotent.

## Verification performed before delivery

An isolated temporary copy of the accepted live snapshot passed installation,
idempotence, simulated HTTPS-failure rollback, and source/protected-file drift
rejection. The old four photo files used preservation fixtures in this test;
this was a byte-preservation check, not a visual-photo comparison.

DOM comparisons confirmed unchanged image URLs, anchor URLs, head links,
metadata, mission text, and booking section. Both the live and Git HTML diffs
are exactly the two authorized removals. No other snapshot file changed.

The removed span is an absolutely positioned overlay. The removed heading's
section supplies its own vertical padding. No desktop/mobile CSS is changed.
Actual public-page visual verification follows the owner's installation;
this document does not claim this package has already been deployed.

The PR is stacked on PR94 (`fix/azmednsk-loading-20261013`) and must remain
unmerged pending the established project workflow.
