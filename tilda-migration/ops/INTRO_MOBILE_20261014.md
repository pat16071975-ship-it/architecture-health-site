# Four mobile introductory blocks

Owner authorization: «Разрешаю исправить вводные мобильные блоки на четырёх
страницах» (14 October 2026). This package addresses mobile audit item M03.

Affected pages: `/o_nas/`, `/uslugi/functional_dentistry/`,
`/uslugi/osteopatiya/`, and `/uslugi/miofascialnaya-terapia/`.

## Problem and change

The introductory copy combined percentage width with a fixed offset from a
320px layout. At a 430px audit frame, the About text extended to x468.34 and
the three service texts to x443.44. Fixed text and background heights also
prevented the section from adapting to wrapped content.

Each page gains one inline style scoped to its first canvas, active only at
viewport widths up to 639px. About uses a natural-height grid for the opening
copy, the two icon/text facts, and the photo. The three service pages use a
vertical flow: photo, introduction, booking button. Their existing background
shape now fills the whole section. Responsive widths keep 15px outer gutters;
service copy has a further 12px inset. Photos retain their files and responsive
aspect ratios, while the booking targets are at least 44px tall.

All body markup, text, font sizes, colors, photos, links, prices and canonical
tags remain byte-for-byte unchanged. No new rules apply at 640px or wider.
Shared stylesheets and other sections are not edited.

## Installation

Standalone installer: `tools/apply_intro_mobile_20261014.py`.
Manifest: `ops/intro_mobile_20261014_manifest.json`.
Base: PR98, commit `fbfaef22e69c4518008249a11d119c6b87159cbc`.
Draft PR stacked on `fix/azmednsk-profile-mobile-20261014`; no merge.

All four target pages were freshly downloaded and matched the accepted PR98
hashes before preparation. The installer checks exact before/after hashes and
86 protected resources, including the accepted profile and phone corrections.
It also compares four earlier approved photos before and after installation.
Historical differences between Git and live HTML are preserved by applying
the same precise replacements to each baseline. Do not deploy the public
directory as a whole.

Guards: root on az-server, fixed site root, shared installation lock, exact
hashes, backup under `/var/backups/azmednsk/intro-mobile-*`, atomic writes with
ownership/mode preservation, HTTPS 200 and exact response bytes, automatic
rollback of written pages on failure, and repeat execution without rewrites.

## Verification

Passed isolated checks: CSS-only scope, identical body markup, original fonts
and image references, identical Git/live replacements, valid scoped selectors,
apply, no-write repeat, protected-resource preservation, complete rollback
after simulated HTTPS failure, and drift rejection for all four targets plus
home, shared CSS, a corrected profile, and patient rules.

Photo-preservation fixtures in the isolated installer test are synthetic and
do not constitute a visual photo check. Prepared CSS has not been published;
no pre-install browser rendering PASS is claimed. After owner installation,
independently check public hashes, the four intros at 320/390/430px, booking
dialog operation, background fit and absence of text collisions, plus desktop
preservation. Check 479/480/639/640 boundaries where browser capability permits.
Do not submit an appointment form during acceptance.

Other mobile audit items, document/form issues, copy edits, and SEO remain
outside this package.
