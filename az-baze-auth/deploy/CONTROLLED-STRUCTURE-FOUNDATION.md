# AZ-BAZE Structure Foundation — Controlled Live Application Contract

Status: PREPARED ONLY. NOT AUTHORIZED FOR LIVE EXECUTION.

## Purpose

Safely apply only migration `20260920_001_structure_foundation` to the existing
`/var/lib/az-baze/auth.db` without deploying new application code and without
running the initial structure seed.

Audited foundation source:

- merged foundation commit: `f8d549df145cd21f54cf17d4e3fa58776aecedf9`
- migration source head before merge: `a5bf22b4e45eb4c6e67fb5edfbe212938c60aa94`
- runner Git blob: `5d5e7f34bcdc711685e8df2791e5b300bfef0d41`
- migration Git blob: `211b9e82a5e46045ab827438ef512c7dcc5a4a6f`

## Safety model

The controlled tool has two modes.

### 1. `--preflight`

Read-only with respect to the live database.

It verifies:

- hostname is `az-server`;
- `az-baze-auth` service is active;
- local `/health` is OK;
- live DB exists;
- `PRAGMA integrity_check` is OK;
- `PRAGMA foreign_key_check` is empty;
- live DB has the exact 17-table pre-foundation table set;
- audited migration source downloads match exact Git blob SHAs;
- migration status is exactly `20260920_001 structure_foundation: pending`.

It does not stop the service, create a backup, apply a migration, seed data, or
deploy application code.

### 2. `--apply`

This mode is intentionally blocked unless the exact confirmation token is
supplied. It must only be run after separate owner authorization.

Order of operations:

1. Repeat all read-only preflight checks.
2. Download and verify exact audited foundation sources.
3. Stop `az-baze-auth` to make the DB quiescent.
4. Capture legacy schema + row-count baseline.
5. Create SQLite online-backup copy in a unique
   `/var/lib/az-baze/backups/structure-foundation-<UTC>/` directory.
6. Verify backup with integrity, foreign keys, schema equality and row counts.
7. Apply only migration `20260920_001`.
8. While service is still stopped, verify:
   - legacy schema is byte-for-byte equal at the SQLite schema-object level;
   - all legacy table row counts are unchanged;
   - only the expected seven new tables exist
     (`schema_migrations` + six foundation tables);
   - all six foundation tables are still empty;
   - migration history contains exactly one expected row;
   - integrity and foreign-key checks pass.
9. Restart service.
10. Verify local health.
11. Recheck runtime integrity, expected table set, empty foundation tables and
    applied migration status without comparing mutable legacy row counts after
    traffic is re-enabled.

## Automatic rollback

If any error occurs after the service has been stopped and a verified backup
exists, the tool automatically:

1. Stops the service if necessary.
2. Verifies the backup again.
3. Removes SQLite sidecar WAL/SHM/journal files while the service is stopped.
4. Restores the verified pre-migration DB with original owner/group/mode.
5. Verifies restored schema, row counts, integrity and foreign keys.
6. Restarts the service and checks health.

This automatic rollback is intended only for the same controlled execution
window. A delayed manual restore of the retained backup is NOT automatic because
it could discard legitimate data written after the migration window.

## Explicit exclusions

This controlled migration does NOT:

- deploy `server.py`, `app.py`, templates or any running application code;
- run `structure_seed.py`;
- insert Holding / Organization / Cluster / Clinic / Direction rows;
- change existing `report_data`, `report_blobs`, `daily_uploads`, Finrez,
  surveys, contacts or IDENT data intentionally;
- create or change user permissions;
- perform a delayed backup restore.

## After a successful migration

The live application should continue to behave exactly as before because no
existing application query uses the new foundation tables yet.

The next step after a separately authorized successful controlled migration is a
separate controlled seed of the current «Архитектура здоровья» structure. That
seed must not be combined with this migration execution.
