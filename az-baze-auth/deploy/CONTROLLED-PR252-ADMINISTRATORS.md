# AZ BAZE — controlled production deployment PR #252

Deployment artifact, not a product change. Audit source HEAD `46f4d9b1c133818c8f0ac1cfdde8bc6640b53a27`; merged branch commit `83405d50bd1a508d4a90641907c8a5586f541147`. The two commits have the **same Git tree** `b17cad58d7dd971f7e5abaa0a7cb473feb4b4af6`.

**Scope:** only eight modified/new runtime files in `/opt/az-baze-auth`; creates exactly six fresh `az_admin_*` SQLite tables and two indexes in `/var/lib/az-baze/auth.db`. No tax logic, historic data backfill, new users or permissions, Finrez, accounting/report recalculations or other system changes.

**Preflight (read-only on product):** requires root, active AZ BAZE service and local health OK, target Python venv, original six live Git blobs exact-matching pre-PR252 baseline, two new files absent, existing DB anchors and integrity/foreign-key checks PASS, new objects absent. Downloads eight files only from the exact immutable merge commit and checks their Git blob hashes; compiles Python/Jinja templates. Extracts DDL by AST, enforces a whitelist of six tables and two indexes, proves schema in an in-memory SQLite DB.

**Apply (authorized controlled release only):** repeats complete preflight; stops `az-baze-auth`; revalidates files/DB; writes private file backups and an SQLite online-backup snapshot under `/var/lib/az-baze/backups/pr252-administrators-<UTC>/`, verifies backup and provenance; atomically installs eight files; creates only the additive schema inside `BEGIN IMMEDIATE`, checking all preexisting schemas and row counts. Restarts app and checks local `/health`, unauthenticated login redirect at `/administrators/`, `/administrators/?tab=payroll` and `/`; validates installed file hashes and DB integrity. Fails closed on state mismatches.

**Rollback:** on error, restarts with the backup version of all existing runtime files, deletes only the two files that were newly introduced. The SQLite backup is retained, but **never automatically restored** — because concurrent timer/other processes could have written legitimate new data after the backup, and overwriting could lose it. The newly created additive tables (if the transaction already committed) remain dormant when old code is restored. Any DB recovery requires separate diagnosis and decision.

**Boundary:** script runs on the AZ BAZE host only, from VS Code remote terminal; not remotely executable through the GitHub/Drive connectors. Source checks and CI do not imply Production changed. An authenticated physical walkthrough on live AZ BAZE still requires the owner after the controlled apply. Never paste passwords or service environment variables into a chat.

**CLI:** `python3 -B <script> --preflight` or `python3 -B <script> --apply`. The operator should fetch the script from an **exact commit SHA**, verify its exact Git blob SHA before running, and provide stdout/stderr log with no secrets.
