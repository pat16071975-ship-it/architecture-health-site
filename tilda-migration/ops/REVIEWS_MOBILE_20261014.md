# Mobile reviews page

Owner authorization: «Разрешаю исправить мобильную страницу «Отзывы»»
(14 October 2026). Scope: audit M04, `/otziv/`.

## Problem and changes

The existing composition uses fixed offsets that place the copy, platform
logos and About button beyond the mobile viewport. A fresh 430px browser
frame reproduced text right441, button right445, and platform logos extending
as far as right557. The background also exceeded the viewport, and the photo
started outside its left edge. The four platform anchors have computed
`pointer-events:none` from the shared shape rule.

One scoped inline style replaces only this composition's positioning at
viewport widths up to 639px. It becomes a natural-height grid: photo, copy,
four platform links in two columns, then the About button. The section has
15px outer gutters and 16px inner padding. The existing background fills the
section; the existing platform panel spans both link rows. Platform hit areas
are at least 44px, and their anchor pointer events are enabled in this mobile
scope. Their destinations and logo files remain unchanged.

The original HTML contains two identical copy variants, a duplicate overlaid
photo, and an off-canvas label. Mobile CSS displays one photo and the same
copy variant as before at each breakpoint: <=479px and 480–639px. The duplicate
photo and already off-canvas label stay hidden in the mobile composition.
No content is deleted. Original font sizes and line heights are retained.

Body markup, all four external review URLs, the About URL, text, image paths,
prices, canonical, and shared CSS remain byte-for-byte unchanged. No new rules
apply at 640px or wider. The existing desktop platform pointer-events issue
is therefore not fixed by this mobile-only package.

## Installation

Installer: `tools/apply_reviews_mobile_20261014.py`.
Manifest: `ops/reviews_mobile_20261014_manifest.json`.
Base PR99: `dee61409b7bd1c11e09eed1de40da060a21414bf`.
Draft stacked on `fix/azmednsk-intro-mobile-20261014`; no merge.

The freshly fetched reviews HTML matched the accepted PR99 hash. Exact
before/after hashes and precise replacements protect historical differences
between Git and live HTML. Only one live HTML file changes; 89 resources from
the accepted PR99 state are protected, plus four earlier photo files compared
before/after apply. Do not deploy the public directory as a whole.

Required guards: root on az-server, fixed root, shared installation lock,
exact hashes, backup under `/var/backups/azmednsk/reviews-mobile-*`, atomic
writes preserving ownership and mode, HTTPS 200 with exact response bytes,
rollback on failure, and repeat execution without rewriting files.

## Verification

Isolated checks passed: removing the style restores the original HTML;
entire body and all five links are unchanged; Git/live replacements match;
selectors refer only to the reviews composition; apply, no-write repeat,
preservation of other files, rollback after simulated HTTPS failure, and
drift rejection for reviews, home, shared CSS and accepted intro/profile/phone
corrections. Four photo fixtures in the isolated test are synthetic and are
not a visual photo test.

The original defect was confirmed in the public browser. New CSS is not yet
published, so pre-install rendering PASS is not claimed. After installation,
check exact public hashes, 320/390/430px layout, visible text and all four
logos, background fit, loaded photo, link hit testing, About navigation and
desktop preservation. Check 479/480/639/640 where browser capabilities permit.
Opening public review destinations is read-only; do not submit a review or
appointment form.

Other audit issues and SEO remain outside this package.
