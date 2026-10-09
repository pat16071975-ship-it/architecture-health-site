# AZ-BAZE PR #74 — Controlled Restoration Deploy Contract

Status: **PREPARED ONLY. DEPLOY NOT AUTHORIZED.**

## Exact target

This deploy package is for the already merged restoration PR #74 only:

- owner-approved restoration source head: `e71a52e84870234a43871571c8ecb41d22cef472`
- merged target commit: `e2c4507653a78fab382c74a5d34100f15674de4a`
- expected Production runtime base: `49bdf49628d252e8eccb3f5a55c3810e650e84c1`

The expected runtime base is the exact PR #71 target that was previously deployed successfully. PR #72 added deploy-package files to GitHub only and did not change Production runtime files.

## Runtime scope

Exactly 12 runtime files are changed:

- `az-baze-auth/attention.py`
- `az-baze-auth/daily_upload.py`
- `az-baze-auth/finrez.py`
- `az-baze-auth/paid_services_upload.py`
- `az-baze-auth/server.py`
- `az-baze-auth/templates/attention.html`
- `az-baze-auth/templates/uploads.html`
- `reports/dashboard.html`
- `reports/finrez.html`
- `reports/forecast.html`
- `reports/index.html`
- `reports/period-view.js`

No tests, GitHub workflows or browser-check scripts are copied to Production.

## Restoration invariants

The deployed target restores only the owner-approved state:

- the old paired clinical upload returns: «Завершённые приёмы» + old «Выручка по направлениям»;
- «Счета и оплаты» remains the separate cash source;
- the new MIS «Выручка по направлениям» remains a separate item 4;
- the new MIS commit stores its own snapshot only and does not replace management data, visits, laboratory orders or `az-service-analytics-v1`;
- historical doctor/direction revenue and average checks remain visible from the approved legacy source;
- Attention and Forecast continue to work without requiring `paidDataComplete`;
- Finrez direction tree uses the approved legacy source during this transition;
- clinic Fact remains cash receipts;
- user-facing «Нераспределённые ДС» stays removed;
- doctor/direction ООО/ИП split stays removed;
- Dashboard plan bar remains the full monthly Plan and Fact remains current cash Fact;
- economics-source discounts / gross revenue remain unchanged;
- paid-services additive tables created by PR #71 are retained.

## Read-only preflight

`--preflight` performs no live code or DB mutation.

It verifies:

- host = `az-server`;
- service `az-baze-auth` is active;
- local `/health` = `ok`;
- DB and site paths are exact;
- all 12 live runtime files match their exact PR #71 base Git blobs;
- all 12 target files download from the exact PR #74 merge commit and match target Git blobs;
- Python syntax passes for all changed Python runtime files;
- Jinja parsing passes for the two changed templates;
- the restored upload/source/report contracts are present;
- user-facing unallocated cash remains absent;
- Dashboard full-month Plan contract remains present;
- DB integrity / foreign keys pass;
- PR #71 paid snapshot tables remain present.

Any mismatch stops before live mutation.

## Apply sequence

Apply is blocked behind the exact confirmation token embedded in the script.

When separately authorized:

1. repeat the entire preflight;
2. stop `az-baze-auth`;
3. prove SQLite and sidecars are quiescent;
4. capture exact logical DB baseline and schema;
5. create a protected backup directory;
6. back up and blob-verify all 12 existing runtime files;
7. create and verify a full SQLite backup;
8. persist baseline, runtime manifest, exact commit provenance, deploy-script blob and DB backup SHA-256;
9. atomically install only the 12 exact target blobs;
10. verify every live target blob;
11. prove the DB logical dump and schema are exactly unchanged before restart;
12. start `az-baze-auth`;
13. verify service active and health = `ok`;
14. verify target blobs again;
15. verify DB integrity / foreign keys and schema unchanged after restart.

## DB rule

PR #74 is a **file-only restoration deploy**.

The deploy script performs no application migration and no business-data write.

The existing paid-services tables are not dropped, rewritten or backfilled.

A verified DB backup is still created as an incident-recovery artifact, but the deploy has no authorized DB mutation.

## Rollback

If any deploy check fails after files begin changing:

- stop the service when necessary;
- restore the exact prior runtime files from the verified backup;
- verify their base Git blobs;
- restart the old service;
- health-check the old service.

The DB is **not automatically restored** because this deploy is file-only and restoring a pre-deploy DB after traffic resumes could lose legitimate writes.

## Explicit exclusions

This package does not:

- run Production preflight;
- run apply/deploy;
- upload either old or new MIS files;
- alter stored snapshots;
- alter historical report data;
- migrate or backfill DB data;
- perform physical browser verification;
- mark the deploy-package PR Ready;
- merge the deploy-package PR.

## Execution rule

Never execute from a mutable branch tip.

After separate owner authorization, use only the exact audited deploy-package commit and verify the deploy-script Git blob before `--preflight`.

The apply step requires a second explicit owner instruction after preflight review.
