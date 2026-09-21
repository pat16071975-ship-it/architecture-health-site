# AZ-BAZE Current Architecture Health — Controlled Structure Seed Contract

Status: PREPARED ONLY. LIVE SEED NOT AUTHORIZED.

## Purpose

Seed the current «Архитектура здоровья» deployment into the already-applied
foundation structure without changing existing business tables, application code,
permissions, reports, Finrez, IDENT, surveys, contacts, or calculations.

This package does **not** create a general multi-customer onboarding workflow.
It creates only the minimum approved current-AZ structure:

- Holding: `Архитектура здоровья`
- Organization: `Архитектура здоровья`
- Cluster: `Кластер по умолчанию`
- Clinic: `Архитектура здоровья`
- Directions: none in this seed

The clinic timezone is intentionally **not guessed or hardcoded**. A valid IANA
timezone must be supplied explicitly at preflight/apply time. The exact seed
spec, including timezone, is SHA-256-bound to the apply confirmation token.

## Preconditions

The live database must already contain the successfully applied foundation
migration:

- version: `20260920_001`
- name: `structure_foundation`

All six foundation data tables must still be empty and their AUTOINCREMENT
sequences must show no previous inserts into the four seed-written tables.

## Exact source provenance

### Seed handoff

Future live execution must begin from this exact handoff:

- commit: `1ea305581904705388bdec8126332a7216e1817a`
- path: `az-baze-auth/tools/controlled_structure_seed_handoff.py`
- Git blob: `a0eab0dabae4cd83b3e28291df0aec781db141fb`

The operator must verify the handoff blob before execution.

### Audited seed controller

The handoff downloads and verifies:

- commit: `23ece9e78ca2cc9571da1599481d2c246edf0a63`
- path: `az-baze-auth/tools/controlled_structure_seed.py`
- Git blob: `be38797d72517d00b23f48058a4914bf620e5048`

Modified controller bytes are refused.

### Pinned helper sources

The seed controller downloads exact sources from:

- source commit: `e271859c4676c28bde595bc7127560e717f1347d`
- `db_migrations.py` blob:
  `5d5e7f34bcdc711685e8df2791e5b300bfef0d41`
- foundation migration blob:
  `211b9e82a5e46045ab827438ef512c7dcc5a4a6f`
- `structure_seed.py` blob:
  `022404705977e8284ebc2c68c87734268cae3cbf`

## Read-only seed preflight

Future `--preflight --timezone <IANA>` mode is read-only with respect to the
working database.

It verifies:

- hostname = `az-server`;
- service `az-baze-auth` active;
- local `/health` = `ok`;
- configured `AZBAZE_DB` resolves to the exact controlled DB path;
- DB integrity and foreign keys;
- exact expected table set:
  17 legacy tables + 6 foundation tables + `schema_migrations`;
- foundation migration status = applied;
- all foundation data tables are empty;
- seed-written foundation AUTOINCREMENT sequences have never been used;
- timezone is a valid IANA zone;
- exact runner / migration / seed-helper blobs match.

It prints:

- exact seed-spec SHA-256;
- exact apply confirmation token bound to that seed spec;
- chosen clinic timezone.

It does not stop the service, create backup, or write to the DB.

## Controlled seed apply

A future apply requires a separate owner authorization and the exact confirmation
token printed by the successful preflight for the same timezone/spec.

Sequence:

1. Repeat full read-only preflight.
2. Check backup-root free capacity.
3. Download and verify exact pinned helper sources.
4. Stop `az-baze-auth`.
5. Prove DB/WAL/SHM/journal quiescence fail-closed through `/proc/*/fd`.
6. Capture exact quiescent baseline:
   - schema objects;
   - row counts of all expected tables;
   - deterministic content hashes of all expected tables;
   - `sqlite_sequence`;
   - integrity / foreign-key checks.
7. Prove quiescence again.
8. Create verified SQLite backup in a unique root-only directory.
9. Record backup SHA-256 and save baseline / backup metadata / exact seed spec.
10. Prove quiescence again.
11. Open one SQLite `BEGIN IMMEDIATE` transaction.
12. Call exact pinned `structure_seed.seed_initial_structure()`.
13. Before commit, verify:
    - exactly 1 Holding;
    - exactly 1 Organization linked to that Holding;
    - exactly 1 default Cluster linked to that Organization;
    - exactly 1 Clinic linked to that Organization and Cluster;
    - clinic timezone equals the approved seed spec;
    - zero Directions;
    - zero Clinic↔Direction links;
    - foundation migration history unchanged;
    - schema unchanged;
    - all tables outside the four intended seed tables have unchanged row counts
      and content hashes;
    - unrelated `sqlite_sequence` state unchanged.
14. Commit only after all in-transaction checks pass.
15. While service is still stopped, repeat full post-seed verification.
16. Prove quiescence again.

All rollback-triggering checks finish before restart.

17. Start service and verify health.
18. Run final read-only seeded-state diagnostics.

## Rollback boundary

Automatic full-DB restore is allowed only before the first post-seed service-start
attempt.

Before restore the controller must:

- verify retained backup SHA-256 exactly;
- verify full baseline schema, row counts, content hashes, sequences, integrity,
  and foreign keys;
- stop service if needed;
- prove DB/WAL/SHM/journal quiescence;
- remove sidecars only while quiescent;
- restore original owner/group/mode;
- verify exact baseline again;
- restart service and health check.

After the first service-start attempt, automatic restore is prohibited because
ordinary traffic may have resumed. A later restore requires a separate incident
decision.

## Explicit exclusions

This controlled seed does NOT:

- deploy application code;
- apply another migration;
- create Directions;
- change existing user permissions;
- change legacy business-table data;
- move reports/Finrez/IDENT/surveys/contacts to the new hierarchy;
- expose new structure UI;
- automatically seed another customer/holding.

## Required unresolved business input

**Clinic timezone is not yet approved.**

Before any live seed preflight, the owner must explicitly provide the correct IANA
timezone for the current clinic. Examples of format only: `Europe/Moscow`,
`Asia/Novosibirsk`, etc. No timezone is to be inferred from server/browser/IP.

## Test contract

Before Ready/merge/live decisions, CI must keep passing:

- foundation regression suite;
- controlled seed suite;
- invalid-timezone rejection;
- seed-spec confirmation binding;
- exact empty-foundation precondition;
- exact seed-row/relationship verification;
- legacy content isolation;
- backup/sequences/mode verification;
- quiescence DB/sidecar guard;
- backup SHA mismatch refusal;
- deliberate pre-restart failure → exact-baseline rollback;
- no automatic restore after restart + simulated traffic;
- exact controller handoff provenance and modified-controller rejection.
