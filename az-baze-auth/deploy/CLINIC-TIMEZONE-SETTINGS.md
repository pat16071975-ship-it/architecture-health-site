# AZ-BAZE Clinic Timezone Settings Contract

Status: PREPARED ONLY. LIVE CODE / DB NOT CHANGED.

## Purpose

Provide one reusable rule and UI mechanism for clinic timezones.

A timezone belongs to the **clinic**, not to the employee, browser, computer,
server, IP address, or current physical location of the person using AZ-BAZE.

Every clinic stores its own IANA timezone in `clinics.timezone`.

## Business rule

- timezone is required for every clinic;
- timezone is selected/configured by the clinic;
- different clinics may use different timezones;
- employee location never changes the clinic timezone;
- browser/device/IP geolocation is not used;
- application code must use the clinic timezone for clinic-local dates, day
  boundaries, schedules, reporting periods, and similar time-dependent logic;
- persisted event timestamps may continue to use UTC where appropriate, then be
  converted through the clinic timezone for clinic-local interpretation.

## Data model

No new migration is required.

The already-applied structure foundation contains:

`clinics.timezone TEXT NOT NULL CHECK (length(trim(timezone)) > 0)`

The settings mechanism adds server-side IANA validation before an existing clinic
timezone can be changed.

## IANA validation

`clinic_timezone.py` is the reusable domain layer.

It provides:

- `normalize_timezone_name()` — validates an explicit IANA timezone;
- `timezone_options()` — server-side list of IANA zones with current UTC offset;
- `get_clinic_timezone()`;
- `set_clinic_timezone()` — updates only `clinics.timezone`, without hidden commit;
- `clinic_local_now()` — converts an explicit/current UTC instant to the clinic
  timezone.

Non-standard namespaces such as `localtime`, `Factory`, `posix/*`,
`right/*`, and `SystemV/*` are rejected.

Malformed/path-like values and oversized inputs are normalized into a
`ClinicTimezoneError` validation failure. The maximum accepted timezone key
length is 255 characters; invalid input fails closed without DB/audit writes.

## UI

The prepared routes are:

- `GET /structure/clinics/` — list clinics and their configured timezone;
- `GET/POST /structure/clinics/<id>/timezone` — edit one clinic timezone.

The timezone form uses a server-provided IANA list and displays the current UTC
offset only as explanatory UI. The saved value is the IANA identifier.

The UI explicitly states that the setting belongs to the clinic and is not based
on the user's current location.

## Permission model

Timezone is a structural clinic setting.

New explicit permission:

`structure_manage`

Rules:

- permission is stored in the existing `permissions` table;
- no permission-table migration is needed;
- `structure_manage` is added to `EXPLICIT_PERMISSION_KEYS`;
- system administrator status alone does **not** grant `structure_manage`;
- a non-admin user may receive `structure_manage` explicitly;
- direct route access without the explicit permission returns 403;
- the permission appears in the existing access-management UI.

This follows the approved rule that structural changes must depend on an explicit
permission, not on job title or role.

## Update transaction / audit

On a valid timezone POST:

1. CSRF is required.
2. The clinic must exist.
3. The form carries the timezone value that was current when the form was rendered.
4. The requested and expected values are both validated server-side as IANA values.
5. The write uses optimistic compare-and-swap:
   `UPDATE clinics SET timezone=? WHERE id=? AND timezone=?`.
6. If another session changed the clinic after the form was opened, the stale write
   affects zero rows and returns HTTP 409 instead of silently overwriting.
7. Therefore the audit `old_timezone` is the exact value that was actually replaced.
8. Only `clinics.timezone` is updated.
9. The existing audit log records:
   - clinic id;
   - previous timezone;
   - new timezone.
10. The timezone update and audit row use the same SQLite connection/transaction.
11. If audit insert/commit fails, the route explicitly rolls the connection back
    before propagating the failure.

Invalid timezone, stale compare-and-swap conflict, CSRF failure, or audit failure
must not leave a partial timezone write.

## Initial seed

The current controlled seed still needs a timezone parameter because the clinic
row does not exist before that first seed.

That parameter is now understood as the **clinic's own configuration value**,
not as the owner's/user's timezone.

Future clinic creation UI must require timezone and reuse the same
`normalize_timezone_name()` validator. No automatic location-based default is
allowed.

## Navigation

Users with explicit `structure_manage` receive a visible
`Настройка клиник` link.

A structure-only user remains able to reach the settings page even when they have
no ordinary content-section permissions.

Section navigation includes `Настройка клиник`.

## Tests

Dedicated CI validates:

- Python syntax;
- Jinja template syntax;
- 17 structure-foundation regression tests;
- clinic timezone domain validation;
- clinic-local time conversion;
- admin does not get `structure_manage` implicitly;
- non-admin may use the page with explicit permission;
- direct access without explicit permission is 403;
- home link appears only with explicit permission;
- valid timezone update changes only `clinics.timezone`;
- update is audited;
- two stale sessions cannot silently overwrite one another;
- conflict response is HTTP 409 and exposes the newly current timezone for retry;
- audit old/new timezone history remains correct after a stale-write conflict;
- path-like and oversized timezone inputs fail closed;
- injected audit failure after timezone UPDATE rolls back both the timezone change
  and the uncommitted audit row;
- invalid timezone writes nothing;
- CSRF is required;
- survey permission regression remains green.

## Explicit exclusions

This preparation does NOT:

- deploy application code;
- change live SQLite;
- run the controlled seed;
- change the existing live clinic timezone;
- infer any clinic timezone;
- create Holding / Organization / Cluster / Clinic rows;
- create Directions;
- convert reports/schedules/Finrez/IDENT or other modules to use the new setting.

Those integrations remain later work and must consume the clinic timezone through
the shared domain rule rather than reimplementing timezone detection.


## Independent-audit hardening — PR4-001…003

The first independent audit found three blockers. The preparation package now
contains fixes for all three:

- **AUDIT-PR4-001** — optimistic compare-and-swap plus an expected-timezone form
  token prevents lost updates and stale audit history. A two-session stale-edit
  integration test verifies that only the first update commits.
- **AUDIT-PR4-002** — malformed/path-like `ZoneInfo` keys and oversized inputs are
  converted to safe validation failures; route tests prove no DB/audit write.
- **AUDIT-PR4-003** — the route explicitly rolls back if the audit step fails after
  the timezone UPDATE. Failure injection inserts an uncommitted audit row and then
  raises; the test proves both the timezone and partial audit row are rolled back.

The admin access page wording was also updated to state that clinic-structure
permissions, like survey permissions, are explicitly granted.

Status after implementation: fixes prepared; repeat independent audit required
before Ready/merge/deploy.
