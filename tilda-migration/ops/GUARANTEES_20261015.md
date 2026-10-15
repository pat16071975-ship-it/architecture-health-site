# Restore state guarantees — 2026-10-15

Owner authorization: «Разрешаю восстановить раздел „Государственные гарантии“ и подготовить исправленный образец договора для согласования».
Base: accepted and deployed PR #112, commit `c08f66fa38d68def22e06f4112aa055e2d502f20`.
Draft PR stacked on `fix/azmednsk-license-link-20261015`; no merge or deployment by Codex.

## Change

Restore one underlined link before the existing back link on `/patient/gosgarantii/`:

- «Территориальная программа государственных гарантий — 2026».
- https://www.novofoms.ru/for_citizens/2664/
- `target="_blank" rel="noopener"`.
- Existing `.prose` typography and natural mobile wrapping.

The TFOMS official page was read on 15.10.2026. It identifies Novosibirsk Government
decree dated 30.12.2025 N 629-п and the program for 2026 and the planning period
2027–2028, with document links. This package links the official publication page;
it does not certify a consolidated legal edition or host the old 572-page scan.

The separately prepared seven-page contract sample is for owner approval only.
It is not in this PR, installer, or public website. `/patient/dogovor/` is protected.

## Installer and verification

`tools/apply_guarantees_20261015.py` changes only `patient/gosgarantii/index.html`.
The manifest protects 88 accepted resources, the absence of three removed
technician profiles, and their nine redirects. Source and live HTML are transformed
independently, preserving historical byte differences. A fresh HTTPS download of
the target page matched the accepted PR #112 snapshot before preparation.

The installer requires root on `az-server`, the fixed production root, and the
existing deployment lock. It validates exact before/after hashes, makes a verified
backup under `/var/backups/azmednsk/guarantees-<UTC>/`, writes atomically preserving
ownership and mode, and verifies served HTTPS bytes at the local origin and the
nine redirects. Verification failure rolls back the changed page. Repeating an
installed package verifies without writes or an additional backup.

Six isolated check groups passed: exact one-link delta; install and idempotence;
rollback on HTTPS failure; rollback on redirect failure; drift guard; absent-profile
guard. HTTP/redirect responses are simulated in the fixture; four legacy photo
fixtures are synthetic. One truncated local acceptance-snapshot PNG was restored
from the earlier snapshot only after matching its unchanged parent manifest hash.
Production files were not changed during preparation. Real HTTPS checks run at
owner installation; deployed desktop/mobile acceptance follows that output.

Photos, five approved prices, contract page, license link, other contacts and links,
canonical, CSS and JavaScript remain unchanged. Nine specialist videos stay deferred.
