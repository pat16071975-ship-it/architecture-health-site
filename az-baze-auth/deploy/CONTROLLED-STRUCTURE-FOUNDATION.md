# AZ-BAZE Structure Foundation — Controlled Live Application Contract

Status: PREPARED ONLY. NOT AUTHORIZED FOR LIVE EXECUTION.

## Purpose

Safely apply only migration `20260920_001_structure_foundation` to the existing
`/var/lib/az-baze/auth.db` without deploying new application code and without
running the initial structure seed.

## Exact provenance chain

The live operator must not execute an arbitrary local controller.

### Handoff entrypoint

Download only:

- repository: `pat16071975-ship-it/architecture-health-site`
- commit: `f425c4f78234d1b9551cdb3ed6fe2502e62920ab`
- path: `az-baze-auth/tools/controlled_structure_foundation_handoff.py`
- Git blob: `4889a7771eee61ef753b792a32e9686438c3b8ef`

Before execution, the handoff file itself must be verified against that Git blob.
The handoff then downloads and verifies the exact audited controller below.

### Audited controller

- commit: `335b71118bc20d3325e0c49fa8c8d1d629188b95`
- path: `az-baze-auth/tools/controlled_structure_foundation_apply.py`
- Git blob: `02d5b8b16de820ebf75537bb6d87730708e94bd5`

The handoff refuses to execute a controller whose Git blob does not match.

### Audited foundation source

- merged foundation commit: `f8d549df145cd21f54cf17d4e3fa58776aecedf9`
- migration source head before merge: `a5bf22b4e45eb4c6e67fb5edfbe212938c60aa94`
- runner Git blob: `5d5e7f34bcdc711685e8df2791e5b300bfef0d41`
- migration Git blob: `211b9e82a5e46045ab827438ef512c7dcc5a4a6f`

The audited controller downloads those exact foundation files and refuses any blob
mismatch.

## Safety model

The controlled tool has two modes.

### 1. `--preflight`

Read-only with respect to the live database.

It verifies:

- hostname is `az-server`;
- `az-baze-auth` service is active;
- local `/health` is OK;
- the database path configured in `/etc/az-baze/auth.env` resolves to the exact
  controlled DB path;
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
2. Verify backup-root free capacity.
3. Download and verify exact audited foundation sources.
4. Stop `az-baze-auth`.
5. Prove database quiescence by scanning `/proc/*/fd` and failing closed if any
   other process has the DB, WAL, SHM, or journal open. If a process FD directory
   cannot be inspected, quiescence is not considered proven.
6. Capture quiescent legacy baseline:
   - complete legacy schema-object snapshot;
   - row counts of all 17 legacy tables;
   - deterministic content hashes of all 17 legacy tables;
   - integrity and foreign-key checks.
7. Prove quiescence again.
8. Create SQLite online-backup copy in a unique
   `/var/lib/az-baze/backups/structure-foundation-<UTC>/` directory.
9. Verify backup:
   - schema equality;
   - row counts;
   - content hashes;
   - integrity and foreign keys.
10. Protect backup:
    - directory mode `0700`;
    - DB file mode `0600`;
    - record exact SHA-256 in memory and metadata.
11. Prove quiescence again.
12. Apply only migration `20260920_001`.
13. While the service is still stopped, verify:
    - legacy schema is unchanged;
    - all legacy row counts are unchanged;
    - all legacy content hashes are unchanged;
    - only the expected new schema objects exist;
    - all six foundation tables are still empty;
    - migration history contains exactly one expected row;
    - integrity and foreign-key checks pass.
14. Prove quiescence again.

At this point all rollback-triggering checks are complete.

15. Start `az-baze-auth`.
16. Verify local health.
17. Run post-restart read-only diagnostics.

## Automatic rollback boundary

Automatic restore is permitted only before the first post-migration service-start
attempt.

If any failure occurs in the pre-restart controlled window and a verified backup
exists, the controller:

1. Verifies the retained backup SHA-256 against the exact digest recorded when
   the backup was created.
2. Verifies the backup schema, row counts, content hashes, integrity, and foreign
   keys.
3. Ensures `az-baze-auth` is stopped.
4. Proves no other process has the DB/WAL/SHM/journal open.
5. Removes SQLite sidecar WAL/SHM/journal files only while the service is stopped.
6. Proves quiescence again.
7. Restores the verified backup with original owner/group/mode.
8. Verifies the restored exact baseline.
9. Starts the service and checks health.

## After the service-start attempt

Once `start_service()` has been attempted, the old backup is never restored
automatically.

Reason: ordinary traffic may already have written new data. Restoring the
pre-migration backup could lose those writes.

If service start/health or post-restart diagnostics fail:

- the controller stops the service when possible after a failed start;
- preserves the current migrated DB and verified backup;
- raises a hard failure for manual incident decision;
- does not restore the old DB automatically.

A later restore requires a separate explicit decision based on current data state.

## Explicit exclusions

This controlled migration does NOT:

- deploy `server.py`, `app.py`, templates or any running application code;
- run `structure_seed.py`;
- insert Holding / Organization / Cluster / Clinic / Direction rows;
- change existing `report_data`, `report_blobs`, `daily_uploads`, Finrez,
  surveys, contacts or IDENT data intentionally;
- create or change user permissions;
- perform a delayed automatic restore after traffic resumes.

## Tests required before live authorization

The controlled suite must remain green and cover:

- exact pre-foundation table-set guard;
- backup verification and protected modes;
- legacy content hashes;
- wrong/extra schema-object detection;
- quiescence guard for DB and sidecars;
- retained backup SHA-256 verification before restore;
- deliberate post-migration failure causing automatic exact-baseline rollback;
- sidecar cleanup during rollback;
- owner/group/mode restoration intent;
- regression proving no automatic restore after service restart / simulated traffic
  write;
- controller provenance handoff accepting the audited controller and rejecting a
  modified controller.

## After a successful migration

The live application should continue to behave exactly as before because no
existing application query uses the new foundation tables yet.

The next step after a separately authorized successful controlled migration is a
separate controlled seed of the current «Архитектура здоровья» structure. That
seed must not be combined with this migration execution.
