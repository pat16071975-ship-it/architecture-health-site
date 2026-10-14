# Mobile specialist profiles and patient-rules phone links

Owner authorization: «Разрешаю исправить мобильные профили специалистов и
четыре телефонные ссылки в правилах пациентов» (14 October 2026).

The mobile audit reproduced text collisions on eleven personal profiles:
Pavlova, Starostenko, Dimitrenko, Mamontova, Chirkov, Bosak, Nazyrov,
Golyshenkov, Lapina, Kholodtseva, and Lyubaeva. Fixed absolute heights let
names, roles, education, prices, and booking controls overlap.

## Changes

Each affected HTML gains one scoped inline style, applying only at viewport
widths up to 639px. The existing profile elements become a vertical flex flow
with natural text heights: photo, name, role, experience, specialization,
education, price, and booking button. Labels stay with their following values.
The profile width is at most 460px with 15px horizontal gutters; the original
photo dimensions are retained at each breakpoint. The button is at least 44px
tall. Text, font sizes, colors, images, prices, experience, links, and DOM
elements remain unchanged. No new CSS applies at 640px or wider.

On `/patient/patient-rules/`, exactly four href values change to
`tel:+73832842421`: two previously dialed +73832496411 and two +73832846011.
Visible legal text and all other markup remain byte-for-byte unchanged.
The separate DMS phone on the About page is outside this change.

## Installation

Standalone installer: `tools/apply_profile_mobile_20261014.py`.
Exact before/after SHA256 and replacements:
`ops/profile_mobile_20261014_manifest.json`.

Only twelve live HTML files change. The installer checks their exact source
hashes and 78 protected resources from the accepted PR96 state, including the
home page, other profiles, CSS, fonts, robots, sitemap, and approved assets.
It also compares the four earlier approved photo files before and after apply.
The twelve target pages and five supporting resources were freshly fetched
before preparation and matched accepted hashes.

Historical Git/live differences remain intact: the same precise replacements
are applied to each baseline. Never deploy the repository public directory as
a whole. The installer does not download site content or check out Git.

Required guards: root on az-server, expected site root, shared
`/run/lock/azmednsk-critical.lock`, exact hashes, backup under
`/var/backups/azmednsk/profile-mobile-*`, atomic writes preserving ownership
and mode, HTTPS 200 with exact response bytes, automatic rollback of written
files on failure, and safe repeat execution.

## Verification

Isolated checks passed:

- Removing each added style restores the original profile HTML exactly;
  the entire body is unchanged. Git and live deltas match.
- The rules page differs by exactly four href values; its text is unchanged.
- Apply, no-write repeat, preservation of other files, and rollback of all
  twelve pages after a simulated HTTPS failure.
- Source drift is rejected for a target profile, rules page, home page,
  shared stylesheet, and protected Zubachev profile.

The isolated photo-preservation checks used synthetic fixtures; they are not
visual photo checks. Local Chromium installation failed because the browser
archive could not be unpacked. No pre-install browser PASS is claimed.

After owner installation, independently check exact public bytes and all
eleven profiles at 320/390/430px, booking-button operation, and desktop
preservation. Check 479/480/639/640 boundaries where browser capability permits.
Narrow desktop frames must not be described as physical phone testing.
Do not submit any appointment or patient form during acceptance.

This package addresses audit M01 and M02 only. The remaining unified audit
items and subsequent SEO work remain open. Draft PR stacked on PR96; no merge.
