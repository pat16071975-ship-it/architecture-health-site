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

- commit: `ab32fb2eb8359a3bd402bf40b199bd9b789e7fb3`
- path: `az-baze-auth/tools/controlled_structure_seed_handoff.py`
- Git blob: `7cba94ec668fa448ae553d20b6863fa3d13b7e4b`

The operator must verify the handoff blob before execution.

### Audited seed controller

The handoff downloads and verifies:

- commit: `81591726f252b23191e557c5a94b3725125d9863`
- path: `az-baze-auth/tools/controlled_structure_seed.py`
- Git blob: `7d3f5e2fff2e51f46c0a720ac7f6ce915d1623e0`

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
- foundation migration status = applied with the exact pinned migration checksum;
- exact foundation table/index/FK/CHECK/UNIQUE schema must match the exact pinned
  migration-derived schema;
- all foundation data tables are empty;
- no AUTOINCREMENT foundation table has any prior sequence use, including
  `directions`;
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
6. Revalidate the exact applied migration checksum and exact pinned
   foundation schema after quiescence.
7. Capture exact quiescent baseline:
   - schema objects;
   - row counts of all expected tables;
   - deterministic content hashes of all expected tables;
   - `sqlite_sequence`;
   - integrity / foreign-key checks.
7. Prove quiescence again.
8. Create verified SQLite backup in a unique root-only directory.
10. Record backup SHA-256 and save baseline / backup metadata / exact seed spec.
11. Prove quiescence again.
12. Revalidate exact migration checksum, exact foundation schema, and pristine
    foundation state immediately before seed.
13. Open one SQLite `BEGIN IMMEDIATE` transaction.
14. Call exact pinned `structure_seed.seed_initial_structure()`.
15. Before commit, verify:
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
16. Commit only after all in-transaction checks pass.
17. While service is still stopped, repeat full post-seed verification,
    including exact foundation schema and migration checksum.
18. Prove quiescence again.

All rollback-triggering checks finish before restart.

19. Start service and verify health.
20. Run final read-only seeded-state diagnostics, including exact foundation
    schema and migration checksum.

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
- rejection of prior AUTOINCREMENT use in any foundation AUTOINCREMENT table,
  including insert+delete history in `directions`;
- exact pinned foundation DDL/constraints/index/FK validation;
- exact applied migration checksum validation after quiescence;
- exact seed-row/relationship verification;
- legacy content isolation;
- backup/sequences/mode verification;
- quiescence DB/sidecar guard;
- backup SHA mismatch refusal;
- deliberate **post-commit / pre-restart** failure → exact-baseline rollback
  of an already committed seed;
- no automatic restore after restart + simulated traffic;
- exact controller handoff provenance and modified-controller rejection.
