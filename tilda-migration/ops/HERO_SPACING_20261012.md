# Home hero spacing

Approved 2026-10-12: fix clipping of the home description and booking button, including mobile compatibility.

Only index.html changes. Scoped inline styles follow existing styles, avoiding another global stylesheet cache update. At desktop widths the description and first action align with the corrected H1 left edge. The second action moves with the first to preserve separation. At 960–1199px description/button widths leave at least 20px before the photo. Below 480px the description keeps at least 10px at the right edge. Mobile vertical positions, font sizes, action labels and targets, stacked actions, photo and other blocks are unchanged.

Install only via tools/apply_hero_spacing_20261012.py --apply on az-server as root. The installer requires exact live post-PR83 hashes, inserts one style block, backs up index.html with metadata, verifies HTTPS, and rolls back on failure. It never uploads the entire Git public snapshot, which differs from the live site. Backup: /var/backups/azmednsk/hero-spacing-*.

Verified: reversible exact style insertion, idempotence, automatic rollback after a simulated HTTPS error, source-drift rejection, horizontal geometry at all integer widths 320–2560px including mobile breakpoints. The geometry checks do not substitute for rendered text/visual QA; actual desktop and mobile browser verification remains required after deployment. No overall site/mobile PASS is claimed.

Test: python3 tilda-migration/tools/test_hero_spacing_20261012.py <post-PR82-snapshot> <live-post-PR83-index.html>
