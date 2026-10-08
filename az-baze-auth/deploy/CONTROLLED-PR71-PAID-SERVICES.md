# AZ-BAZE PR #71 — Controlled Deploy Contract

Status: **PREPARED ONLY. DEPLOY NOT AUTHORIZED.**

## Scope

This package deploys only the already-merged PR #71 state:

- source PR head: `186598f723a096ac63cb61ee04e79d85866e73db`
- merged target commit: `49bdf49628d252e8eccb3f5a55c3810e650e84c1`
- expected live/base commit state: `742d4b68bf471be32c5a072e5d646f79eeab5e8d`

It installs only the 20 runtime files listed in the deploy script manifest.

It does **not** deploy test files, GitHub workflows, browser-check scripts, or any unrelated repository files.

## Production invariants

The deploy preserves the approved source split:

- clinic Fact and clinic-wide ООО/ИП stay sourced from «Счета и оплаты»;
- doctor/direction amounts use the new MIS «Оплачено» source;
- total primary/repeat remains sourced from «Завершённые приёмы»;
- ambiguous patient/day cross-direction matches are not guessed;
- user-facing «Нераспределённые ДС» is removed from doctor/direction management views, while the internal cash field is not destroyed;
- Finrez gross/discount source can use the economists’ «Выручка» sheet;
- Dashboard Plan bar is the full monthly plan, Fact bar is current cash Fact.

## Additive database change

PR #71 requires two new paid-services storage tables and two explicit indexes:

- `service_payment_snapshots`
- `service_payment_versions`
- `idx_service_payment_snapshots_month`
- `idx_service_payment_versions_date`

The controlled deploy creates only those objects.

Before applying the schema delta it verifies that the target schema contains no INSERT / UPDATE / DELETE / DROP / ALTER / REPLACE statements.

The deploy records the complete pre-change legacy schema and row counts, then verifies after migration that:

- every legacy schema object is byte-for-byte identical at the SQL-definition level;
- every legacy table row count is unchanged;
- the only new non-internal schema objects are the four objects above;
- both new tables are empty;
- `PRAGMA integrity_check` is OK;
- `PRAGMA foreign_key_check` is empty.

## Preflight

`--preflight` makes no live code or DB changes.

It requires:

- host `az-server`;
- active service `az-baze-auth`;
- local health = `ok`;
- DB = `/var/lib/az-baze/auth.db`;
- site root = `/var/www/az-baze.ru`;
- every existing live runtime file matches the exact PR #71 base Git blob;
- all three new Python modules are absent;
- target files download from the exact merge commit and match exact target Git blobs;
- Python syntax passes;
- Jinja parsing passes;
- source-contract guards pass;
- live DB integrity / foreign keys pass;
- paid-services tables do not already exist.

Any mismatch stops before live mutation.

## Apply sequence

Execution is additionally blocked by an exact confirmation token.

When separately authorized, apply performs:

1. the full preflight again;
2. stops `az-baze-auth`;
3. proves the SQLite DB and sidecars are quiescent;
4. captures the legacy DB schema and row counts;
5. backs up every existing runtime file and verifies its base Git blob;
6. creates and verifies a SQLite backup in
   `/var/lib/az-baze/backups/pr71-paid-services-<UTC>/`;
7. stores the captured DB baseline, runtime manifest, exact commit provenance and backup SHA-256 inside the protected backup directory;
8. installs only the exact target runtime blobs;
9. applies only the additive paid-services schema;
10. verifies the exact DB schema delta and unchanged legacy row counts;
11. starts `az-baze-auth`;
12. checks local health;
13. verifies all live target Git blobs again;
14. verifies DB integrity and exact schema delta again.

## Rollback policy

Before the first service-start attempt:

- any failure restores all existing files;
- removes the three newly introduced Python modules;
- restores the verified DB backup;
- removes SQLite sidecars only while the service is stopped;
- restarts the old service and checks health.

After a service-start attempt:

- the old DB is **not** automatically restored because traffic could have written new data;
- target files are rolled back to the prior live blobs;
- the additive paid-services tables are left in place;
- the prior application is restarted and health-checked.

This avoids losing writes after traffic may have resumed. The old application ignores the additive tables.

## Post-deploy data activation

A successful code/schema deploy does not itself populate the new sources.

Immediately after deploy:

- clinic Fact and clinic-wide ООО/ИП continue from the already stored «Счета и оплаты» layer;
- paid doctor/direction metrics intentionally show no new paid-source value until the new MIS «Выручка по направлениям» file is uploaded through its separate control;
- the paid-services tables are present but empty;
- existing completed-visit history remains present;
- the economics JSON created by the pre-PR71 parser does not yet contain the new `revenue` subsection, so Finrez gross/discount switches to the economists’ canonical source only after the economics workbook is explicitly re-imported.

The first paid-services upload and the economics-workbook re-import are **data operations, not part of deploy**. They require their own operator action after the deployed UI/health is verified. No deploy script silently uploads, backfills, or rewrites those business sources.

## Explicit exclusions

This preparation does not:

- run preflight on the server;
- stop or restart any live service;
- change Production files;
- change Production DB;
- upload the new MIS report;
- backfill historical paid-service snapshots;
- upload economists’ workbook;
- merge the deploy-package PR;
- authorize deploy.

The visible upload journal remains outside this deploy scope and is unchanged.

## Execution rule

Never execute from a mutable branch tip.

When deployment is separately authorized, download the deploy script from the **exact audited deploy-package commit SHA** and verify its Git blob before running `--preflight`. The apply command must be a separate owner instruction after preflight is reviewed.
