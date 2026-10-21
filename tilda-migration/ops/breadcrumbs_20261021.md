# Breadcrumb completion — 2026-10-21

Owner requested completion after the live audit. OG previews were explicitly skipped.

Scope: 73 internal sitemap pages. Keep the existing navigation placement and CSS. Add a non-link current-page label, normalize doctor parent label to Команда, add Главная / Лаборатория, correct the implantation parent to the existing Хирургия и имплантация section, and fix the old О клинике breadcrumb pointing to home. Add a BreadcrumbList matching each visible trail. Existing self-links are replaced by current-page labels. Parent URLs are normalized to their canonical trailing slash form. Homepage and non-sitemap retired/thank-you pages stay unchanged.

All content outside breadcrumbs and the new JSON-LD is byte-equivalent after DOM normalization. Git-source differences from live remain intact; deployment uses exact live hashes and replacement counts, not full source deployment. Static checks cover matching schema, current labels, canonical URLs, parent membership, unique destinations, exact doctor-name spacing, and source reversibility.

Installer checks 393 protected hashes, runtime photo snapshots and absent profiles, backs up to /var/backups/azmednsk/breadcrumbs-*, patches only 73 HTML files, verifies server-local HTTPS and nine redirects, and rolls back owned writes on exceptions. Seven rehearsal scenarios passed, including idempotence and partial-write/HTTPS/redirect rollback. Rehearsal HTTP callbacks are simulated.

Browser evidence: preview at 3f3336f198361cbd38cf86d41daf5fe999febe11, caries at widths320/390/640/1366 with loaded fonts and no horizontal overflow; ceramic-crowns chain at320 with loaded fonts, no overflow and screenshot reviewed. Browser testing found concatenated doctor names from H1 line breaks; all affected names were fixed and statically reverified before d210685fc643ebddd0664bf0c0db412ddfdf1ac0. The final-preview URL returned generic HTTP429 on initial load and one reload. No completed final-version five-page visual pass, physical phone check or Google Rich Results Test is claimed. Preview-only font URLs use pinned GitHub files to avoid cross-origin font restrictions; production font markup is unchanged.

Production not changed by this preparation. Installer pinned at d210685fc643ebddd0664bf0c0db412ddfdf1ac0, SHA256269ed647aa543912d5a539d23bcf73c2fbea06cec4de1b684b2ff2d526e60c4c.
