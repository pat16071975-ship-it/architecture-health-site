# Paying-patient economics in the existing two views

Source and tests only. No merge, Production deployment, data re-import, permissions changes or advertising changes have been performed.

## Periods and sources

The two approved choices remain exactly `Сравнение` and `На дату`. Date comparison keeps its established same-day monthly cutoffs. The existing quarter, half-year and year modes keep their original periods. No third choice is added.

The read-only API now exposes every provable month-to-date payer endpoint as `paidPatientsByDate`, not just the latest monthly endpoint. The latest monthly summary is selected from those proven endpoints; a complete chain of an initial month-to-date export and later daily slices advances it. Gaps, future monthly totals and missing evidence never become fabricated earlier values. Only aggregate counts leave the API; patient tokens and private confirmation fingerprints remain private.

Both date views use the same exact-period count and matching displayed direction revenue for averages. Switching back to `На дату` refreshes the actual selected record. A copied earlier record retains its source date and cannot supply a falsely current medicine numerator. The existing mobile helper's obsolete primary-visit denominator note is corrected.

The established latest-confirmed doctor-money fallback now handles both legacy snapshots and explicit stored intervals. A newer clinical/cash day no longer discards an earlier complete MIS interval. It is not relabelled as a newer exact payer source.

Quarter/half/year economics now use the corresponding confirmed monthly sources and weighted sums, not a mean of monthly averages. The pre-existing aggregate denominator remains a sum of monthly unique payer counts, not a claim that it is the number of distinct people across the entire year. Direction money coverage must equal the coverage of its denominators. Medicine retains `factMedicine`; no alternative numerator is silently introduced.

## Common expenses

The original common expense numerator is divided by all medical payers. A direction/doctor's burden uses its own payer count multiplied by that common monthly unit cost. Complete monthly expenses cannot use partial-month payer counts. Missing expenses and a zero overall denominator do not create zero expense or fictitious profit. Ambiguous doctor surnames are not guessed.

## Evidence

Runtime commit `aa4d10259faff4d61479f0cfcf932d5edcf67b7c` was produced only after source-preparation run `40847147539`, job `123357322521`, completed all steps successfully:

- 170 Python tests executed, including actual SQLite/report API tests (the imported historical fixture class is also discovered by unittest).
- 22 existing two-view/cost JavaScript scenarios plus 8 new endpoint/period scenarios; 16 history, 14 period and 5 upload scenarios retained.
- 74 browser assertions on the actual rendered management page and full JavaScript helpers, widths 1365 and 390. No browser JavaScript errors or write requests.
- Synthetic data only. No live UI, live January–October totals or independent-auditor acceptance claimed.

Artifact `12885497101` ZIP SHA256 `7eb68dd9e9b465acfacf2f85adbc001fd3b47ffbacb65ed77494b99ff889b4fe` was downloaded and verified. Source files were matched by Git blob hashes. A fresh PR run is required on the final source/documentation HEAD.

## Limits and protected scope

A full-month patient list still cannot prove the list on an earlier day27. This source correction does not fill such unavailable historical cutoffs and must not be presented as closing that data/acceptance gap. It does make every genuinely available endpoint and supported existing period usable without changing source dates or asking for the same uploaded files again.

Six runtime files: `report_storage.py`, `index.html`, `period-view.js`, `management-mobile-layout.js`, `services-primary-patient-cost.js`, `services.html`. All backend changes are read-only projections; schema, imports, original payloads, private confirmation file, cash Fact/ООО/ИП, clinical values, lab formula and permissions remain untouched.

CARC as the last advertising row, advertising date controls, persistent upload-result feedback and the inactive-doctor grouping remain separate pending work. No advertising formula or UI is installed by this PR.
