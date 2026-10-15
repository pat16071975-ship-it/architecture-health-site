# Cookie links and empty team filter — 2026-10-15

Owner authorization: «Разрешаю исправить две ссылки в cookie и убрать пустую вкладку „Администраторы“».

## Scope

- `/cookie/`: change the two `href="https://expertstom.ru/"` values whose visible label is `https://azmednsk.ru/` to `href="https://azmednsk.ru/"`. Preserve all visible legal text and other attributes.
- `/team/`: remove the single empty `Администраторы` filter button (`data-part="289374994093"`). The same markup serves desktop and mobile. Keep all 17 cards, their order and classification, and the other 11 filters byte-for-byte. Keep the overall label `Врачи`.

No shared CSS/JS, photos, prices, other legal pages, sitemap, nginx, or technician redirects change. Removed technician profiles stay absent. Other audit findings require separate authorization.

## Delivery

Stacked Draft PR on accepted PR108, commit `a2b83c49944cb4b9f731e6631ca3be940e365805`. Do not merge or deploy the whole public directory: historical source and production HTML differ.

Run the self-contained `tools/apply_cookie_admin_filter_20261015.py --apply` on `az-server` as root. The installer applies only exact literal transformations to the two current production files. Its manifest contains production before/after SHA256 values, 87 protected resources, three absent technician profiles and nine preserved redirects.

The installer checks the hostname, UID, fixed site root and exclusive deployment lock; rejects unexpected file versions; preserves file ownership and modes; creates a timestamped backup under `/var/backups/azmednsk/cookie-admin-filter-*`; writes atomically; checks exact local HTTPS output and existing 301 redirects; rolls back its writes on failed verification. A successful repeat makes no changes and no new backup. Four additional approved legacy photos are compared before and after.

## Verification

Preparation evidence is in scratch `az-cookie-admin-live-20261015/verification.json` and `az-cookie-admin-prepared-20261015/checks.json`:

- Current 89 resources must match the accepted PR108 manifest before building the package.
- Confirm exact source/production transformations, unchanged cookie text, unchanged 17 cards, and 11 remaining filters each matching at least one card.
- Exercise apply, idempotent repeat, HTTPS/redirect failure rollback, refusal on unexpected target/CSS/price changes, and refusal if deleted technician pages reappear.

HTTP and redirects are simulated in isolated installer tests; real server verification runs during installation. Browser acceptance of the new desktop/mobile version follows owner installation. No form submissions are involved.
