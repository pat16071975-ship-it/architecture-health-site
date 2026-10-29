# PR164 — controlled paid-patient history delivery

Status: preparation only. The owner authorized merge of PR164 at exact HEAD `83512c73deee5d04703465d6efc131e7b7f61e30` and preparation of this package, explicitly NOT its Production installation.

Merged target: `ffd0a876562c3b9c1e74410d9cf644aafb3fff39`.
Required prior runtime: the four manifest positions at base `2a0301e28d01a7ebaeae81e1e96b9ce4362930b7` (the new helper must be absent).
The merge tree is identical to the approved source HEAD. No application code is changed by this ops branch.

## Installation scope

Exactly four runtime files: `paid_period_confirmation.py`, `report_storage.py` under `/opt/az-baze-auth`; `index.html`, `period-view.js` under `/var/www/az-baze.ru/reports`.

One new PRIVATE configuration: `/var/lib/az-baze/paid-period-confirmations-v1.json`, root:www-data 0640, readable by the existing service account. It is not stored in Git or the public website. Existing user roles, access rules and service configuration are not modified. An existing unknown configuration or a newer/mixed runtime causes STOP, never an overwrite.

Only these reporting-month declarations are allowed: January 05–31 -> January; February 02–28 -> February; March 02–31 -> March; May 01–30 -> May. Exact import stamps come from the owner's previously supplied server output via private stdin. Source-file SHA256 and the exact payload SHA256 are calculated using read-only SQLite, never invented. All four sources must be present, unambiguous and have complete patient evidence. This package does not certify arbitrary short uploads as full months. October is not normalized.

## Preflight (the only currently permitted operator step)

`python3 -B <verified-installer> --preflight` reads a JSON array of four explicit selections from stdin. Each has exactly `kind`, `sourceFrom`, `sourceTo`, `importedAt`, `reportMonth`. Do not upload real selections/configuration to the public repository.

Preflight checks root/host, existing service account/group, service health, exact runtime blobs, backup capacity, exact merged downloads and target Python syntax. JavaScript is also checked when Node is installed; otherwise it reports `CI_ONLY_NODE_NOT_INSTALLED`, not a false server JS PASS.

SQLite is opened with `mode=ro`, `query_only=ON` and a consistent read transaction. App/server modules, schema initialization, HTTP login, uploads and migrations are never invoked. The exact standalone helper and AST-extracted report reading functions check all four monthly payer results and corresponding money periods. No patient names/tokens, source payloads or source hashes are printed. Aggregate counts and the proposed configuration SHA256 are printed.

The fingerprint checks schema and every table, with row multiplicity preserved. Preflight creates only temporary source staging files, which are removed; it does not install the private configuration, create a persistent backup, stop/start the service or write business data. Required markers: `PAID_HISTORY_PREFLIGHT=PASS`, `DATABASE_WRITES=NONE`, `ACTIVE_CONFIG_WRITES=NONE`, `SERVICE_ACTIONS=NONE`, `LIVE_CHANGES=NONE; APPLY_NOT_STARTED`.

## Apply — separate owner authorization required

The prepared interface is `--apply PAID-HISTORY-FFD0A876 <reviewed-CONFIG_SHA256>`, with the same explicit selections on stdin. Do not execute it under the current preparation-only authorization.

Before any stop, the installer repeats preflight and verifies the reviewed configuration hash. Under the shared release lock it stops only `az-baze-auth`, checks MainPID=0, repeats source/period checks and rejects drift. It makes a private checkpoint under `/var/lib/az-baze/backups/paid-history-...`: exact prior files/metadata, verified SQLite backup, private configuration, manifest and installer. Backup files/directories are synced before replacement.

Four target blobs are installed atomically per file, preserving prior uid/gid/mode. The new helper inherits report_storage metadata. The private configuration is installed without clobbering an existing file; service-account readability is checked before start. Full DB fingerprints and protected neighboring Python/HTML/JS files are compared before and after service start. Source dates, payloads, provider registry, money, clinical statistics and other calculations are not rewritten.

Expected final markers: `PAID_HISTORY_PRODUCTION_DEPLOY=PASS`, `CHANGED_RUNTIME_FILES=4`, `PRIVATE_CONFIG_FILES=1`, `FULL_DATABASE_UNCHANGED=PASS`, `PROTECTED_RUNTIME_UNCHANGED=PASS`, `SERVICE=active; HEALTH=ok`, `XLSX_IMPORT=NOT_PERFORMED`.

## Recovery and rollback

An installation/health failure after changes triggers restoration of the three previous files plus removal of this exact newly created helper/configuration. Newer unknown runtime/configuration and corrupt checkpoints cause a refusal instead of destructive overwrite.

Manual interface, requiring separate authorization: `--rollback <exact-checkpoint> PAID-HISTORY-FFD0A876`. It checks checkpoint ownership/mode, release identity, backup hashes, current runtime and protected-file drift before stopping the service.

**No automatic or manual mode in this installer restores SQLite from backup.** Legitimate uploads made after the checkpoint remain intact. A concurrent external DB write may conservatively stop delivery and trigger file-only recovery; that write is not erased. Uncatchable termination can leave mixed files; the checked checkpoint supports a subsequent explicitly authorized file-only rollback, not a blind rerun of apply.

## Verification boundary

25 isolated package tests cover actual filesystem writes, metadata, SQLite backup/fingerprints, exact read-only report functions, incomplete installation, stale sources/configuration, health failure, missing disk space, unreadable configuration, newer runtime/configuration, corrupt backup, and preservation of later DB rows during automatic/manual recovery. Host, network, systemd and service-user execution are mocked. No live Production test or independent acceptance audit is claimed.

Source PR164 has six successful workflows on the approved exact HEAD. Delivery CI separately verifies merge/source equality, all four base/target manifest blobs, the ops-only diff, syntax and these package tests. Neither CI nor preparation invokes Production installation.

After a later separately authorized successful installation, verify monthly history using the existing `Итоги месяцев` selector. Keep `На эту дату по месяцам` distinct: a full monthly patient list is not evidence for day 27. No XLSX re-upload is required. PR163 popup/grouping changes remain separate and are not included.
