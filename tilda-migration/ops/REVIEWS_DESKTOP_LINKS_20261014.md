# Restore desktop review-platform links

Owner approval: «Разрешаю исправить четыре ссылки на площадки отзывов в компьютерной версии».

On `/otziv/`, the four existing platform anchors inherit the shared
`.visual-shape .visual-inner { pointer-events:none }` rule. Mobile interaction
was restored by PR100, while the desktop links still reject pointer input.

This change appends one inline style, `az-reviews-desktop-links-20261014`,
with `@media (min-width:640px)`. Four exact selectors restore
`pointer-events:auto` on the 2GIS, Flamp, Yandex and Google anchors only.
There are no layout, image, color, font, text, URL or canonical changes.
The accepted mobile stylesheet and entire HTML body remain byte-identical.

## Controlled installation

- Parent: PR100, `db98879a2a6ce89c37b8a024a88651bf70173044`.
- Installer: `tools/apply_reviews_desktop_links_20261014.py`.
- Manifest: `ops/reviews_desktop_links_20261014_manifest.json`.
- One target HTML plus 89 protected resources from accepted PR100 state.
- Four earlier approved photos are also compared before/after installation.
- Fixed host `az-server`, root user, fixed public directory, shared lock.
- Exact input/output hashes; atomic write preserving ownership and mode.
- Backup under `/var/backups/azmednsk/reviews-desktop-links-*`.
- HTTPS response must exactly match installed bytes; failure rolls back.
- Repeating an installed package verifies HTTPS without writing files.
- Never deploy the complete public directory from Git: historical Git/live
  differences are preserved by applying the same narrow replacement.

## Validation

PASS in isolated rehearsal: exact source hashes, unchanged body and all five
section links, unchanged mobile style, identical Git/live change, installation,
repeat without writes, protection of other resources, rollback after simulated
HTTPS failure, refusal on target/protected-file drift. Earlier photo files in
the isolated rehearsal are synthetic preservation fixtures, not a visual test.

The deployed page has not yet changed. Browser acceptance follows owner
installation: verify public hashes, desktop pointer/hit targets for all four
anchors, preserved positions, and mobile 2x2 layout. Do not submit a review or
appointment form. External destination content is outside this change.
