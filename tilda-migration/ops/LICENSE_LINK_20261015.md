# Restore the clinic license PDF link — 2026-10-15

Owner authorization: «Разрешаю восстановить ссылку на лицензию клиники».
Base: accepted and deployed PR #111, commit `b9a78eda26509f87b5a8051885268764be5890c4`.
Keep this as a Draft PR stacked on `fix/azmednsk-policy-links-20261015`.
The owner installs the pinned script on `az-server`; no merge or remote deployment by Codex.

## Change

The migrated `/patient/licens/` page has a heading and a back link but no license document.
Add one underlined link immediately before the existing back link:

- Label: «Выписка из реестра лицензий от 13.05.2026 (PDF)».
- URL: https://drive.google.com/file/d/14V6G9_j0ZguP8smWXh_IM_96qPGGWN7b/view
- `target="_blank" rel="noopener"`.
- Existing `.prose` typography, natural wrapping and mobile font sizing.

This is the same file ID as the original Tilda license iframe. The three-page
document is «Лицензия мед деят ЭКСПЕРТ.pdf», a register extract dated 13.05.2026
for ООО «Экспертстом»: ОГРН 1195476066070, ИНН 5406800409,
license Л041-01125-54/00359148. Its licensed locations include Ленина, 85.
The link states the extract date; this work does not certify the current registry status.

The Drive preview renders all three pages and exposes Download. Public access
without sign-in is enabled. A separate anonymous HTTP download returned 200
and a valid 382446-byte, three-page PDF:
SHA256 `bd27bbf3214eced98214739494fa2a84402701dc9710b25d7a41bc6fd38ca27e`.
No document edits or sharing changes were made.

## Installer and verification

`tools/apply_license_link_20261015.py` changes only `patient/licens/index.html`.
Its manifest protects the accepted bytes of 88 other resources, the absence of
three removed technician profiles, and their nine redirects to `/team/`.
Source HTML and production HTML are transformed independently to retain their
historical byte differences. The fresh production license page matched the
accepted PR #111 snapshot before preparation.

The installer requires root on `az-server`, the fixed production root and the
existing deployment lock. It checks exact before/after hashes, makes a verified
backup in `/var/backups/azmednsk/license-link-<UTC>/`, writes atomically while
preserving ownership and mode, and verifies the served HTTPS bytes at the local
origin plus the preserved redirects. A verification failure rolls back the
changed page. Repeating an installed package verifies without writing or making
another backup. Price and photo assets, contacts, canonical, CSS and JS are untouched.

Isolated fixture checks cover the exact one-link HTML delta, preserved anchors,
assets and metadata, successful install, idempotence, rollback on HTTPS and
redirect failure, and refusal on unexpected protected/source changes or a
reappeared technician profile. Fixture HTTP/redirect responses are simulated;
the four legacy photo fixtures are synthetic. Real HTTPS is enforced on install.
Visual acceptance of the deployed link on desktop and narrow screens follows
the owner's installation output.
