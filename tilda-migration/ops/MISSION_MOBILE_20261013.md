# Mobile home mission spacing — 2026-10-13

The owner approved correcting the overlap between the mission copy and the
«О клинике» link on the home page. PR95 acceptance reproduced it at frame
widths 320, 390, and 430: the text occupied 444px while the link began 429px
below the text's top, causing about 15px of overlap.

## Change

Only `public/index.html` changes. A scoped inline style applies below 640px,
using the existing mobile breakpoint. The existing four mission elements
become grid items: copy, link, and image occupy consecutive rows; the existing
background spans the copy and link. The link follows the natural text height
with a 16px gap. The image follows the background with a 10px gap.

The narrow card keeps its 300px maximum width and 285px copy width; the next
mobile breakpoint keeps a 460px maximum width and 440px copy width. Existing
font sizes, weights, colors, text, link destination, image dimensions and
image source are retained. The shape and section heights adapt to the content.
Above 639px none of these new rules applies. No DOM elements are moved and no
shared stylesheet is changed. Removing the added style restores the original
Git and live HTML byte for byte.

## Guarded installation

Pinned standalone installer: `tools/apply_mission_mobile_20261013.py`.

- Before home SHA256: `68954b5de3282236ca3f372dc6a5d3c0f4308a1a1de71c75d6988b58d6eedd50`
- After home SHA256: `10429335abc2b1f1fe81187b0f5b67f1b622539cc53232a39d0782eb0f4fd6cd`
- Installer SHA256: `0fd3522015a9845bf2976e709f85dc0b5dcf6edf3e6b0cd22438012313195070`

Before preparation, fresh home, layouts.css, and site.css requests matched
the accepted PR95 snapshot. The installer guards 89 other resources using
their accepted hashes, including the mission image. It additionally compares
the four previously approved photo files before and after installation.

The installer runs only as root on az-server, takes the shared deployment
lock, backs up under `/var/backups/azmednsk/mission-mobile-*`, writes only the
home atomically while preserving owner/mode, and verifies HTTPS 200 plus the
exact expected HTML. It restores the original file on failure unless a
concurrent edit prevents safe rollback. A repeated apply is idempotent.
Never deploy the whole repository public directory: historical Git/live
differences are intentionally preserved by the exact live patch.

## Verification

Isolated-copy checks passed: apply, idempotence, simulated HTTPS-failure
rollback, and rejection of drift in the home, Pavlova page, layouts.css, and
mission image. Body HTML, metadata, links and images are identical. All other
snapshot files stay unchanged. The four older photos use byte-preservation
fixtures in this isolated test; it is not a photo visual comparison.

Pre-install browser rendering of the new CSS is not claimed. Public desktop
and mobile visual acceptance follows the owner's installation: check 320,
390, 430, the 479/480/639/640 breakpoint boundaries, and desktop; confirm
the full text, link gap, background coverage, image and following reviews.
Narrow desktop-browser frames are not physical-phone tests.

This draft PR is stacked on PR95, `fix/azmednsk-home-cleanup-20261013`.
No merge or live installation has been performed during preparation.
