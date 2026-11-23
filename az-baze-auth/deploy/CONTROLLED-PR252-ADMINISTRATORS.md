# AZ BAZE — controlled production deployment PR #252

Deployment artifact, not a product change. Audit source HEAD `46f4d9b1c133818c8f0ac1cfdde8bc6640b53a27`; merged branch commit `83405d50bd1a508d4a90641907c8a5586f541147`. The two commits have the **same Git tree** `b17cad58d7dd971f7e5abaa0a7cb473feb4b4af6`.

**Scope:** only eight modified/new runtime files in `/opt/az-baze-auth`; creates exactly six fresh `az_admin_*` SQLite tables and two indexes in `/var/lib/az-baze/auth.db`. No tax logic, historic data backfill, new users or permissions, Finrez, accounting/report recalculations or other system changes.

**Preflight (read-only on product):** requires root, active AZ BAZE service and local health OK, target Python venv, original six live Git blobs exact-matching pre-PR252 baseline, two new files absent, existing DB anchors and integrity/foreign-key checks PASS, new objects absent. Downloads eight files only from the exact immutable merge commit and checks their Git blob hashes; compiles Python/Jinja templates. Extracts DDL by AST, enforces a whitelist of six tables and two indexes, proves schema in an in-memory SQLite DB.

**Apply (authorized controlled release only):** repeats complete preflight; stops `az-baze-auth`; revalidates files/DB; writes private file backups and an SQLite online-backup snapshot under `/var/lib/az-baze/backups/pr252-administrators-<UTC>/`, verifies backup and provenance; atomically installs eight files; creates only the additive schema inside `BEGIN IMMEDIATE`, checking all preexisting schemas and row counts. Restarts app and checks local `/health`, unauthenticated login redirect at `/administrators/`, `/administrators/?tab=payroll` and `/`; validates installed file hashes and DB integrity. Fails closed on state mismatches.

**Rollback:** on error, restarts with the backup version of all existing runtime files, deletes only the two files that were newly introduced. The SQLite backup is retained, but **never automatically restored** — because concurrent timer/other processes could have written legitimate new data after the backup, and overwriting could lose it. The newly created additive tables (if the transaction already committed) remain dormant when old code is restored. Any DB recovery requires separate diagnosis and decision.

**Boundary:** script runs on the AZ BAZE host only, from VS Code remote terminal; not remotely executable through the GitHub/Drive connectors. Source checks and CI do not imply Production changed. An authenticated physical walkthrough on live AZ BAZE still requires the owner after the controlled apply. Never paste passwords or service environment variables into a chat.

**CLI:** `python3 -B <script> --preflight` or `python3 -B <script> --apply`. The operator should fetch the script from an **exact commit SHA**, verify its exact Git blob SHA before running, and provide stdout/stderr log with no secrets.

## Live knowledge_catalog preservation (preflight STOP resolved in package)

Owner confirmed only this development thread works on AZ BAZE. A verified read-only live server comparison found exactly two pre-existing lines missing from the PR252 Git baseline:

```python
import knowledge_catalog
knowledge_catalog.register(app)
```

Live `server.py` SHA is **`8c9a1b45390e273a1e8f1bc8a12f331cf0fbdd47`**, while the earlier release installer mistakenly expected `7d186761a727baa1fe50707c80b3d9ab154a3b96`. No other one of the eight runtime files differs; the two new files remain absent. The first live `--apply` stopped in preflight, before stopping services or modifying data. It must NOT be rerun.

The corrected installer now requires **exactly that verified live server Git blob**, preserves the existing `knowledge_catalog` module in place, downloads the audited merged server blob **`b0934c61c722ab006a405d1aaefebf4cd4a86fbb`**, and inserts only those two existing registration lines at verified anchors. Its resulting exact pinned server blob is **`99dbe7ac9bd35bcc131d0c321b0d269716c4ceb9`**, containing both `knowledge_catalog.register(app)` and `administrators.register(app)`. Any mismatch, unexpected edited source, missing local knowledge module, or duplicate registration causes a hard STOP before changes. If rollback is required, the verified live server version with the knowledge catalog registration is restored, NOT the older Git baseline.

The deploy package fixture CI checks the expected live server hash from the original Git baseline plus exactly the two observed lines, the resulting combined target hash, knowledge module preservation, original runtime rollback, protection from unrecognised changes, and isolated additive DB migration. **The audited PR252 source remains unchanged.** The preserved two-line live patch is an intentional compatibility layer of the installer and is not silently treated as identical to the reviewed Git source tree.
