# Historical paid patients: reporting periods, not service-row boundaries

This change uses the existing stored patient-summary evidence. It does not
reimport XLSX, rewrite source dates/payloads, migrate SQLite or replace clinical
or cash sources. Patient names and tokens are not added to the reporting API.

A monthly export may have service rows starting after the first calendar day or
ending before the last. That alone is NOT proof of a complete month. For the
historic exports explicitly confirmed by the owner, a private sidecar binds the
reporting month to the exact stored source kind, original bounds, source SHA256,
payload SHA256 and import timestamp. A changed/replaced upload invalidates the
binding. Arbitrary short slices remain short; a long export is not split.

The sidecar is `paid-period-confirmations-v1.json` next to the configured DB,
not in the public web root or Git. `tools/paid-history-confirmation-plan.py`
prepares a separate private proposal from explicit selections. It opens SQLite
read-only and refuses to write the active configuration. No real clinic data,
source identifiers or owner-specific confirmations belong in this repository.
Publishing the checked source and installing the private binding require a
separate controlled release. This PR does neither.

Patient counts and provider amounts share the same reporting-period reader.
Money is not recomputed; provider mapping, counts from unique patient-summary
tokens, old-debt payments and laboratory exclusions remain as before. An exact
calendar-boundary upload takes precedence over a historic alias. Overlaps are
not added twice. Missing patient evidence does not fall back to visits or
partial service details. Derived payer fields in API copies cannot use stale
stored numbers when the underlying evidence is absent; stored rows are untouched.

The existing `Просмотр` selector gains `Итоги месяцев` in the same table/layout.
It shows each month's actual loaded cutoff (including a partial current month).
The default `На эту дату по месяцам` and `Одна дата` remain unchanged; a complete
month is never presented as a result on day 27. The monthly selector joins the
raw revenue record at exactly the payer cutoff. If it is absent, no general
average/cash/visit row is manufactured. Direction averages require a complete
provider-paid cover for that same interval, not an older or cash fallback.

Scope excludes upload-confirmation popup and collapsed former-employee rows;
those remain separately approved pending UI work. No production, merge or data
backfill is performed by this source/test change.
