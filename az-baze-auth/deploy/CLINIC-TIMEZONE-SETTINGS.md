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
3. The IANA timezone is validated server-side.
4. Only `clinics.timezone` is updated.
5. The existing audit log records:
   - clinic id;
   - previous timezone;
   - new timezone.
6. The timezone update and audit record are committed together through the
   existing audit transaction.

Invalid timezone or CSRF failure makes no clinic write.

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
