# Personal-data policy link corrections — 15 October 2026

Owner command: «Разрешаю исправить ошибочные ссылки в политике обработки персональных данных».

Base: accepted live PR110, commit `7cd3022df61b9148da1a2fb7e3692fde3605aa9a`. Stacked Draft PR; owner installation on `az-server`, no automatic merge or deployment.

## Change

Only `/politica-site/` changes. Three logical link corrections occur twice each: in the policy prose and in the existing link list.

| Existing link | Correct destination |
| --- | --- |
| `https://expertstom.ru/` | `https://azmednsk.ru/`, with the displayed URL updated |
| `https://netology.ru/legal/11`, labelled «целей обработки персональных данных» | `#personal-data-purposes`, the existing section 2 of this policy |
| A printed policy address split between two anchors, `/politica/` and `https://expertstom.ru/politica/` | One complete `https://azmednsk.ru/politica-site/` link |

The introduction also contains one unlinked `https://expertstom.ru/` reference identifying the site covered by this policy. This stale website URL becomes `https://azmednsk.ru/`, consistently with the corrected homepage links. No surrounding wording is changed.

Section 2 receives one unique `id="personal-data-purposes"` on its existing strong heading. The purposes links navigate within this page instead of opening a different organization's policy in a new tab. The current-policy URL points back to `/politica-site/`; `/politica/` is a different page titled «Политика конфиденциальности». Existing homepage links retain their new-tab behavior; external links outside this correction retain `target="_blank" rel="noopener"`.

The legal prose is byte-preserved except for the displayed URLs above. Legal terms, requisites, postal addresses, email, consent language and the Garant reference are not edited. There is no legal-compliance claim. No CSS, JavaScript, photos, prices, booking forms or other pages change. The laboratory PDF link from PR110 remains protected; specialist videos remain deferred.

## Evidence and installation

- PR110 acceptance immediately before this task verified 89 production resources and nine technician redirects. A fresh download of the target policy is checked against that accepted hash before preparing this package.
- The source HTML and live HTML have historical differences. Identical narrow replacements are applied independently; repository HTML never overwrites the live page wholesale.
- Installer: `tools/apply_policy_links_20261015.py`. Manifest: `ops/policy_links_20261015_manifest.json`.
- One HTML target, 88 protected resources, three absent technician profiles, nine preserved redirects and before/after snapshots of four legacy approved photos.
- Requires root on `az-server`, fixed production root and existing deployment lock; exact input/output hashes, atomic replacement with preserved file ownership/mode, backup under `/var/backups/azmednsk/policy-links-<UTC>`, exact local-origin HTTPS response validation and automatic rollback.
- Repeating an installed package verifies without rewriting the page or making another backup.
- Isolated fixture checks cover the exact source/live delta, unique valid fragment target, preserved legal text apart from URL labels, preserved unrelated links/assets, first install, no-write repeat, rollback on HTTPS/redirect failure, drift refusal and absence guards. HTTPS/redirect responses are simulated in fixtures and verified on the actual server by the installer; four legacy photos have synthetic fixture bytes.

After owner installation, verify the live links and section-2 jump in desktop and narrow browser layouts. This preparation does not claim browser acceptance of a not-yet-deployed page.

In the VS Code terminal connected to `az-server`, enter root with `sudo -i`, then use the provided command to download the script pinned to the PR commit, check its SHA256 and execute `python3 <script> --apply`. Send the terminal result for external acceptance.
