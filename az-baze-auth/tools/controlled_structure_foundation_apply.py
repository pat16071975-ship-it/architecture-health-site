#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SERVICE = "az-baze-auth"
EXPECTED_HOSTNAME = "az-server"
DB_PATH = Path("/var/lib/az-baze/auth.db")
BACKUP_ROOT = Path("/var/lib/az-baze/backups")
ENV_FILE = Path("/etc/az-baze/auth.env")

FOUNDATION_COMMIT = "f8d549df145cd21f54cf17d4e3fa58776aecedf9"
FOUNDATION_VERSION = "20260920_001"
FOUNDATION_NAME = "structure_foundation"
RUNNER_BLOB = "5d5e7f34bcdc711685e8df2791e5b300bfef0d41"
MIGRATION_BLOB = "211b9e82a5e46045ab827438ef512c7dcc5a4a6f"

APPLY_CONFIRMATION = f"APPLY-STRUCTURE-FOUNDATION-{FOUNDATION_COMMIT[:12]}"

LEGACY_TABLES = {
    "audit_log",
    "credential_trash",
    "credentials",
    "daily_uploads",
    "permissions",
    "report_blobs",
    "report_data",
    "survey_answers",
    "survey_invites",
    "survey_options",
    "survey_questions",
    "survey_responses",
    "survey_sections",
    "surveys",
    "upload_log",
    "users",
    "work_contacts",
}

FOUNDATION_TABLES = {
    "holdings",
    "organizations",
    "clusters",
    "clinics",
    "directions",
    "clinic_directions",
}

NEW_TABLES = FOUNDATION_TABLES | {"schema_migrations"}

FOUNDATION_INDEXES = {
    "idx_organizations_holding",
    "idx_clusters_organization",
    "idx_clinics_organization",
    "idx_clinics_cluster",
    "idx_directions_organization",
    "idx_clinic_directions_organization",
}


class ControlledApplyError(RuntimeError):
    pass


def step(message):
    print(f"\n=== {message} ===", flush=True)


def check(condition, message):
    if not condition:
        raise ControlledApplyError(message)


def git_blob_sha(data):
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configured_database_path(env_file=ENV_FILE):
    source = Path(env_file)
    check(source.exists() and source.is_file(), f"environment file missing: {source}")
    configured = None
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if not line.startswith("AZBAZE_DB="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        configured = value
    target = Path(configured) if configured else DB_PATH
    check(target.is_absolute(), f"AZBAZE_DB must be absolute: {target}")
    return target.resolve()


def precheck_backup_capacity(db_path=DB_PATH, backup_root=BACKUP_ROOT):
    root = Path(backup_root)
    check(root.exists() and root.is_dir(), f"backup root missing: {root}")
    check(os.access(root, os.W_OK | os.X_OK), f"backup root is not writable: {root}")
    db_size = Path(db_path).stat().st_size
    free = shutil.disk_usage(root).free
    required = max(db_size * 3, 64 * 1024 * 1024)
    check(
        free >= required,
        f"insufficient backup free space: free={free} required={required}",
    )
    return {"db_bytes": db_size, "free_bytes": free, "required_bytes": required}


def _connect_ro(path):
    target = Path(path).resolve()
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _user_tables(conn):
    return {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _schema_objects(conn):
    return {
        f"{row['type']}:{row['name']}": {
            "type": row["type"],
            "name": row["name"],
            "tbl_name": row["tbl_name"],
            "sql": row["sql"],
        }
        for row in conn.execute(
            """
            SELECT type,name,tbl_name,sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
              AND type IN ('table','index','trigger','view')
            ORDER BY type,name
            """
        )
    }


def _row_counts(conn, tables):
    return {
        table: conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
        for table in sorted(tables)
    }


def _integrity_checks(conn):
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    check(integrity == "ok", f"integrity_check failed: {integrity}")
    foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    check(not foreign_keys, f"foreign_key_check failed: {len(foreign_keys)} row(s)")


def preflight_current_database(db_path=DB_PATH):
    target = Path(db_path)
    check(target.exists() and target.is_file(), f"DB missing: {target}")
    conn = _connect_ro(target)
    try:
        _integrity_checks(conn)
        tables = _user_tables(conn)
        check(
            tables == LEGACY_TABLES,
            "unexpected live table set; expected pristine pre-foundation schema: "
            f"missing={sorted(LEGACY_TABLES - tables)} extra={sorted(tables - LEGACY_TABLES)}",
        )
        return {
            "tables": sorted(tables),
            "row_counts": _row_counts(conn, LEGACY_TABLES),
        }
    finally:
        conn.close()


def capture_quiescent_baseline(db_path=DB_PATH):
    target = Path(db_path)
    conn = _connect_ro(target)
    try:
        _integrity_checks(conn)
        tables = _user_tables(conn)
        check(tables == LEGACY_TABLES, "quiescent baseline table set changed")
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tables": sorted(tables),
            "row_counts": _row_counts(conn, LEGACY_TABLES),
            "schema_objects": _schema_objects(conn),
        }
    finally:
        conn.close()


def verify_exact_baseline(db_path, baseline):
    conn = _connect_ro(db_path)
    try:
        _integrity_checks(conn)
        check(_user_tables(conn) == set(baseline["tables"]), "backup/rollback table set mismatch")
        check(
            _row_counts(conn, baseline["tables"]) == baseline["row_counts"],
            "backup/rollback row-count mismatch",
        )
        check(
            _schema_objects(conn) == baseline["schema_objects"],
            "backup/rollback schema mismatch",
        )
    finally:
        conn.close()


def backup_database(db_path, backup_path, baseline):
    source = Path(db_path)
    destination = Path(backup_path)
    check(not destination.exists(), f"backup target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(destination.parent, 0o700)

    src = _connect_ro(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    os.chmod(destination, 0o600)
    verify_exact_baseline(destination, baseline)
    return {
        "path": str(destination),
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
    }


def verify_postapply(db_path, baseline):
    conn = _connect_ro(db_path)
    try:
        _integrity_checks(conn)
        tables = _user_tables(conn)
        expected_tables = LEGACY_TABLES | NEW_TABLES
        check(
            tables == expected_tables,
            f"postapply table set mismatch: missing={sorted(expected_tables - tables)} "
            f"extra={sorted(tables - expected_tables)}",
        )

        current_schema = _schema_objects(conn)
        expected_schema_keys = (
            set(baseline["schema_objects"])
            | {f"table:{table}" for table in NEW_TABLES}
            | {f"index:{index}" for index in FOUNDATION_INDEXES}
        )
        check(
            set(current_schema) == expected_schema_keys,
            "postapply schema-object set mismatch: "
            f"missing={sorted(expected_schema_keys - set(current_schema))} "
            f"extra={sorted(set(current_schema) - expected_schema_keys)}",
        )
        for key, before in baseline["schema_objects"].items():
            check(current_schema.get(key) == before, f"legacy schema changed: {key}")

        current_counts = _row_counts(conn, LEGACY_TABLES)
        check(current_counts == baseline["row_counts"], "legacy row counts changed")

        for table in sorted(FOUNDATION_TABLES):
            count = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
            check(count == 0, f"foundation table must remain empty before seed: {table}={count}")

        migration_rows = conn.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall()
        check(len(migration_rows) == 1, f"schema_migrations row count={len(migration_rows)}")
        check(
            migration_rows[0]["version"] == FOUNDATION_VERSION
            and migration_rows[0]["name"] == FOUNDATION_NAME,
            f"unexpected migration history: {dict(migration_rows[0])}",
        )

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        baseline_indexes = {
            item["name"]
            for item in baseline["schema_objects"].values()
            if item["type"] == "index"
        }
        check(
            FOUNDATION_INDEXES <= indexes,
            f"foundation indexes missing: {sorted(FOUNDATION_INDEXES - indexes)}",
        )
        check(
            baseline_indexes <= indexes,
            f"legacy indexes missing: {sorted(baseline_indexes - indexes)}",
        )

        return {
            "tables": sorted(tables),
            "legacy_row_counts": current_counts,
            "migration": {
                "version": migration_rows[0]["version"],
                "name": migration_rows[0]["name"],
            },
        }
    finally:
        conn.close()


def verify_runtime_postapply(db_path):
    conn = _connect_ro(db_path)
    try:
        _integrity_checks(conn)
        tables = _user_tables(conn)
        expected_tables = LEGACY_TABLES | NEW_TABLES
        check(
            tables == expected_tables,
            f"runtime table set mismatch: missing={sorted(expected_tables - tables)} "
            f"extra={sorted(tables - expected_tables)}",
        )

        for table in sorted(FOUNDATION_TABLES):
            count = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
            check(count == 0, f"foundation table unexpectedly seeded: {table}={count}")

        migration_rows = conn.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall()
        check(
            len(migration_rows) == 1
            and migration_rows[0]["version"] == FOUNDATION_VERSION
            and migration_rows[0]["name"] == FOUNDATION_NAME,
            "runtime migration history mismatch",
        )
    finally:
        conn.close()


def _download(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AZ-BAZE-Structure-Controlled-Apply/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def download_foundation_sources(target_dir):
    root = Path(target_dir)
    migration_dir = root / "migrations"
    migration_dir.mkdir(parents=True, exist_ok=True)

    items = [
        (
            "db_migrations.py",
            RUNNER_BLOB,
            root / "db_migrations.py",
        ),
        (
            "migrations/v20260920_001_structure_foundation.py",
            MIGRATION_BLOB,
            migration_dir / "v20260920_001_structure_foundation.py",
        ),
    ]

    for relative, expected_blob, destination in items:
        url = (
            "https://raw.githubusercontent.com/"
            "pat16071975-ship-it/architecture-health-site/"
            f"{FOUNDATION_COMMIT}/az-baze-auth/{relative}"
        )
        data = _download(url)
        actual_blob = git_blob_sha(data)
        check(
            actual_blob == expected_blob,
            f"source blob mismatch for {relative}: {actual_blob} != {expected_blob}",
        )
        compile(data, str(destination), "exec")
        destination.write_bytes(data)

    return root / "db_migrations.py", migration_dir


def load_migration_runner(path):
    spec = importlib.util.spec_from_file_location("az_controlled_db_migrations", path)
    check(spec is not None and spec.loader is not None, "cannot load migration runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_pending_status(runner, db_path, migration_dir):
    status_rows = runner.migration_status(db_path, migration_dir)
    check(
        status_rows
        == [{"version": FOUNDATION_VERSION, "name": FOUNDATION_NAME, "state": "pending"}],
        f"unexpected preapply migration status: {status_rows}",
    )


def verify_applied_status(runner, db_path, migration_dir):
    status_rows = runner.migration_status(db_path, migration_dir)
    check(
        status_rows
        == [{"version": FOUNDATION_VERSION, "name": FOUNDATION_NAME, "state": "applied"}],
        f"unexpected postapply migration status: {status_rows}",
    )


def _run(command, check_result=True):
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    if check_result and process.returncode != 0:
        raise ControlledApplyError(
            f"command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout={process.stdout}\nstderr={process.stderr}"
        )
    return process


def service_is_active():
    return _run(["systemctl", "is-active", "--quiet", SERVICE], check_result=False).returncode == 0


def local_health_ok():
    process = _run(
        ["curl", "-fsS", "--max-time", "10", "http://127.0.0.1:8001/health"],
        check_result=False,
    )
    return process.returncode == 0 and process.stdout.strip() == "ok"


def stop_service():
    _run(["systemctl", "stop", SERVICE])
    check(not service_is_active(), f"{SERVICE} did not stop")


def start_service():
    _run(["systemctl", "start", SERVICE])
    for _ in range(20):
        if service_is_active() and local_health_ok():
            return
        time.sleep(0.5)
    raise ControlledApplyError(f"{SERVICE} failed health check after start")


def restore_backup(db_path, backup_path, baseline, original_stat):
    target = Path(db_path)
    backup = Path(backup_path)
    check(backup.exists(), f"rollback backup missing: {backup}")
    verify_exact_baseline(backup, baseline)

    stop_service() if service_is_active() else None

    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    restore_tmp = target.with_name(target.name + ".rollback.tmp")
    shutil.copy2(backup, restore_tmp)
    os.chown(restore_tmp, original_stat.st_uid, original_stat.st_gid)
    os.chmod(restore_tmp, stat.S_IMODE(original_stat.st_mode))
    os.replace(restore_tmp, target)

    verify_exact_baseline(target, baseline)
    start_service()


def controlled_preflight(db_path=DB_PATH):
    check(socket.gethostname() == EXPECTED_HOSTNAME, f"hostname must be {EXPECTED_HOSTNAME}")
    configured = configured_database_path()
    check(
        configured == Path(db_path).resolve(),
        f"service database path mismatch: configured={configured} controlled={Path(db_path).resolve()}",
    )
    check(service_is_active(), f"{SERVICE} is not active")
    check(local_health_ok(), "local health check failed")
    summary = preflight_current_database(db_path)

    with tempfile.TemporaryDirectory(prefix="az-structure-preflight-") as temp:
        runner_path, migration_dir = download_foundation_sources(temp)
        runner = load_migration_runner(runner_path)
        verify_pending_status(runner, db_path, migration_dir)

    return summary


def controlled_apply(db_path=DB_PATH):
    check(os.geteuid() == 0, "apply must run as root")
    controlled_preflight(db_path)
    precheck_backup_capacity(db_path, BACKUP_ROOT)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / f"structure-foundation-{timestamp}"
    backup_path = backup_dir / "auth-before-foundation.db"
    baseline_path = backup_dir / "baseline.json"
    backup_meta_path = backup_dir / "backup-meta.json"

    original_stat = Path(db_path).stat()
    service_stopped = False
    backup_ready = False
    baseline = None

    with tempfile.TemporaryDirectory(prefix="az-structure-apply-") as temp:
        runner_path, migration_dir = download_foundation_sources(temp)
        runner = load_migration_runner(runner_path)
        verify_pending_status(runner, db_path, migration_dir)

        try:
            step("QUIESCE SERVICE")
            stop_service()
            service_stopped = True

            step("CAPTURE QUIESCENT BASELINE")
            baseline = capture_quiescent_baseline(db_path)

            step("BACKUP + VERIFY")
            backup_meta = backup_database(db_path, backup_path, baseline)
            backup_ready = True
            baseline_path.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            backup_meta_path.write_text(
                json.dumps(backup_meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            check(sha256_file(backup_path) == backup_meta["sha256"], "backup sha256 changed")

            step("APPLY FOUNDATION MIGRATION")
            result = runner.apply_migrations(db_path, migration_dir)
            check(
                result == {"applied": [FOUNDATION_VERSION], "already_applied": []},
                f"unexpected migration result: {result}",
            )

            step("POSTAPPLY DATABASE VERIFICATION")
            verify_applied_status(runner, db_path, migration_dir)
            verify_postapply(db_path, baseline)

            step("RESTART + HEALTH")
            start_service()
            service_stopped = False

            step("FINAL READ-ONLY VERIFICATION")
            verify_applied_status(runner, db_path, migration_dir)
            # After restart, ordinary application traffic may legitimately change
            # legacy row counts. Recheck integrity/schema state without comparing
            # those counts to the quiescent baseline, avoiding a rollback that
            # could discard legitimate post-restart writes.
            verify_runtime_postapply(db_path)

        except Exception:
            if backup_ready and baseline is not None:
                print("\nCONTROLLED APPLY FAILED: automatic rollback starting", file=sys.stderr)
                try:
                    restore_backup(db_path, backup_path, baseline, original_stat)
                    service_stopped = False
                    print("AUTOMATIC ROLLBACK: PASS", file=sys.stderr)
                except Exception as rollback_exc:
                    print(f"AUTOMATIC ROLLBACK: FAIL: {rollback_exc}", file=sys.stderr)
                    raise
            elif service_stopped:
                try:
                    start_service()
                    service_stopped = False
                except Exception as restart_exc:
                    print(f"SERVICE RESTART AFTER PRE-MIGRATION FAILURE FAILED: {restart_exc}", file=sys.stderr)
            raise

    print("\nCONTROLLED STRUCTURE FOUNDATION APPLY: PASS")
    print(f"BACKUP={backup_path}")
    print(f"BACKUP_SHA256={sha256_file(backup_path)}")
    print("SEED=NOT_RUN")
    print("LIVE_APP_CODE=NOT_DEPLOYED")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Controlled first application of AZ-BAZE structure foundation migration"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Read-only checks only")
    mode.add_argument("--apply", action="store_true", help="Backup + controlled migration apply")
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.preflight:
        step("READ-ONLY PREFLIGHT")
        summary = controlled_preflight(DB_PATH)
        print(f"LEGACY_TABLES={len(summary['tables'])}")
        print("MIGRATION_STATUS=PENDING")
        print("CONTROLLED PREFLIGHT: PASS")
        return 0

    check(
        args.confirm == APPLY_CONFIRMATION,
        f"apply confirmation mismatch; required: {APPLY_CONFIRMATION}",
    )
    return controlled_apply(DB_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
