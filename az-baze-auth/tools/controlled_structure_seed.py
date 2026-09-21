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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SERVICE = "az-baze-auth"
EXPECTED_HOSTNAME = "az-server"
DB_PATH = Path("/var/lib/az-baze/auth.db")
BACKUP_ROOT = Path("/var/lib/az-baze/backups")
ENV_FILE = Path("/etc/az-baze/auth.env")

SOURCE_COMMIT = "e271859c4676c28bde595bc7127560e717f1347d"
RUNNER_BLOB = "5d5e7f34bcdc711685e8df2791e5b300bfef0d41"
MIGRATION_BLOB = "211b9e82a5e46045ab827438ef512c7dcc5a4a6f"
SEED_HELPER_BLOB = "022404705977e8284ebc2c68c87734268cae3cbf"

FOUNDATION_VERSION = "20260920_001"
FOUNDATION_NAME = "structure_foundation"

HOLDING_NAME = "Архитектура здоровья"
ORGANIZATION_NAME = "Архитектура здоровья"
CLUSTER_NAME = "Кластер по умолчанию"
CLINIC_NAME = "Архитектура здоровья"

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

ALL_TABLES = LEGACY_TABLES | FOUNDATION_TABLES | {"schema_migrations"}
SEED_WRITTEN_TABLES = {"holdings", "organizations", "clusters", "clinics"}
FOUNDATION_AUTOINCREMENT_TABLES = {
    "holdings",
    "organizations",
    "clusters",
    "clinics",
    "directions",
}
UNCHANGED_TABLES = ALL_TABLES - SEED_WRITTEN_TABLES


class ControlledSeedError(RuntimeError):
    pass


def step(message):
    print(f"\n=== {message} ===", flush=True)


def check(condition, message):
    if not condition:
        raise ControlledSeedError(message)


def git_blob_sha(data):
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_timezone(value):
    name = str(value or "").strip()
    check(bool(name), "clinic timezone is required")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ControlledSeedError(f"unknown IANA timezone: {name}") from exc
    return name


def seed_spec(timezone_name):
    timezone_name = validate_timezone(timezone_name)
    return {
        "holding": {
            "name": HOLDING_NAME,
            "short_name": "",
            "status": "active",
        },
        "organization": {
            "name": ORGANIZATION_NAME,
            "short_name": "",
            "status": "active",
        },
        "cluster": {
            "name": CLUSTER_NAME,
            "description": "",
            "status": "active",
        },
        "clinic": {
            "name": CLINIC_NAME,
            "short_name": "",
            "region": "",
            "city": "",
            "address": "",
            "timezone": timezone_name,
            "status": "active",
        },
        "directions": [],
    }


def seed_spec_digest(spec):
    payload = json.dumps(
        spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def required_confirmation(spec):
    return f"APPLY-CURRENT-AZ-SEED-{seed_spec_digest(spec)[:16]}"


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


def _connect_rw(path):
    conn = sqlite3.connect(Path(path))
    conn.row_factory = sqlite3.Row
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


def _stable_cell(value):
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, bool):
        return ["int", int(value)]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", repr(value)]
    return ["text", str(value)]


def _table_content_hash(conn, table):
    info = list(conn.execute(f'PRAGMA table_info("{table}")'))
    columns = [row["name"] for row in info]
    pk_columns = [
        row["name"]
        for row in sorted(
            (row for row in info if int(row["pk"]) > 0),
            key=lambda row: int(row["pk"]),
        )
    ]
    order_columns = pk_columns or columns
    quoted_columns = ", ".join(f'"{name}"' for name in columns)
    quoted_order = ", ".join(f'"{name}"' for name in order_columns)
    query = f'SELECT {quoted_columns} FROM "{table}"'
    if quoted_order:
        query += f" ORDER BY {quoted_order}"

    digest = hashlib.sha256()
    for row in conn.execute(query):
        encoded = json.dumps(
            [_stable_cell(row[name]) for name in columns],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _table_content_hashes(conn, tables):
    return {table: _table_content_hash(conn, table) for table in sorted(tables)}


def _sqlite_sequences(conn):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if not exists:
        return {}
    return {
        row["name"]: row["seq"]
        for row in conn.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name")
    }


def _foundation_schema_snapshot(conn):
    objects = {
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
              AND (
                    (type='table' AND name IN (
                        'holdings','organizations','clusters',
                        'clinics','directions','clinic_directions'
                    ))
                    OR
                    (type='index' AND tbl_name IN (
                        'holdings','organizations','clusters',
                        'clinics','directions','clinic_directions'
                    ))
                  )
            ORDER BY type,name
            """
        )
    }
    foreign_keys = {}
    for table in sorted(FOUNDATION_TABLES):
        foreign_keys[table] = [
            tuple(row)
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        ]
    return {
        "objects": objects,
        "foreign_keys": foreign_keys,
    }


def expected_foundation_schema(migration_path):
    module = load_module(migration_path, "az_seed_expected_foundation_migration")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        module.upgrade(conn)
        return _foundation_schema_snapshot(conn)
    finally:
        conn.close()


def verify_exact_foundation_schema(conn, expected_schema):
    actual = _foundation_schema_snapshot(conn)
    check(
        actual == expected_schema,
        "live foundation schema does not match exact pinned migration",
    )


def migration_file_checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_exact_migration_history(conn, migration_path):
    rows = conn.execute(
        "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    check(len(rows) == 1, f"schema_migrations row count={len(rows)}")
    expected_checksum = migration_file_checksum(migration_path)
    check(
        rows[0]["version"] == FOUNDATION_VERSION
        and rows[0]["name"] == FOUNDATION_NAME
        and rows[0]["checksum"] == expected_checksum,
        "foundation migration history/checksum mismatch",
    )


def _integrity_checks(conn):
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    check(integrity == "ok", f"integrity_check failed: {integrity}")
    foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    check(not foreign_keys, f"foreign_key_check failed: {len(foreign_keys)} row(s)")


def _foundation_tables_empty(conn):
    counts = _row_counts(conn, FOUNDATION_TABLES)
    check(
        all(count == 0 for count in counts.values()),
        f"foundation tables are not empty: {counts}",
    )
    sequences = _sqlite_sequences(conn)
    touched = {
        table: sequences[table]
        for table in FOUNDATION_AUTOINCREMENT_TABLES
        if table in sequences and int(sequences[table]) > 0
    }
    check(not touched, f"foundation AUTOINCREMENT sequence already used: {touched}")


def verify_foundation_migration_row(conn):
    rows = conn.execute(
        "SELECT version,name FROM schema_migrations ORDER BY version"
    ).fetchall()
    check(len(rows) == 1, f"schema_migrations row count={len(rows)}")
    check(
        rows[0]["version"] == FOUNDATION_VERSION
        and rows[0]["name"] == FOUNDATION_NAME,
        f"unexpected migration history: {dict(rows[0])}",
    )


def capture_baseline(
    db_path=DB_PATH,
    *,
    expected_schema=None,
    migration_path=None,
):
    conn = _connect_ro(db_path)
    try:
        _integrity_checks(conn)
        tables = _user_tables(conn)
        check(
            tables == ALL_TABLES,
            f"unexpected table set: missing={sorted(ALL_TABLES - tables)} "
            f"extra={sorted(tables - ALL_TABLES)}",
        )
        verify_foundation_migration_row(conn)
        if migration_path is not None:
            verify_exact_migration_history(conn, migration_path)
        if expected_schema is not None:
            verify_exact_foundation_schema(conn, expected_schema)
        _foundation_tables_empty(conn)
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tables": sorted(tables),
            "schema_objects": _schema_objects(conn),
            "row_counts": _row_counts(conn, ALL_TABLES),
            "content_hashes": _table_content_hashes(conn, ALL_TABLES),
            "sqlite_sequences": _sqlite_sequences(conn),
            "expected_foundation_schema": expected_schema,
            "migration_checksum": (
                migration_file_checksum(migration_path)
                if migration_path is not None
                else None
            ),
        }
    finally:
        conn.close()


def verify_exact_baseline(db_path, baseline):
    conn = _connect_ro(db_path)
    try:
        _integrity_checks(conn)
        check(_user_tables(conn) == set(baseline["tables"]), "baseline table set mismatch")
        check(
            _schema_objects(conn) == baseline["schema_objects"],
            "baseline schema mismatch",
        )
        check(
            _row_counts(conn, baseline["tables"]) == baseline["row_counts"],
            "baseline row-count mismatch",
        )
        check(
            _table_content_hashes(conn, baseline["tables"]) == baseline["content_hashes"],
            "baseline content-hash mismatch",
        )
        check(
            _sqlite_sequences(conn) == baseline["sqlite_sequences"],
            "baseline sqlite_sequence mismatch",
        )
    finally:
        conn.close()


def backup_database(db_path, backup_path, baseline):
    destination = Path(backup_path)
    check(not destination.exists(), f"backup target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(destination.parent, 0o700)

    src = _connect_ro(db_path)
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


def verify_seeded_state(conn, spec, baseline=None, strict_unchanged=False):
    _integrity_checks(conn)
    check(_user_tables(conn) == ALL_TABLES, "seed changed table set")
    if baseline is not None:
        check(_schema_objects(conn) == baseline["schema_objects"], "seed changed DB schema")

    counts = _row_counts(conn, FOUNDATION_TABLES)
    expected_counts = {
        "holdings": 1,
        "organizations": 1,
        "clusters": 1,
        "clinics": 1,
        "directions": 0,
        "clinic_directions": 0,
    }
    check(counts == expected_counts, f"unexpected foundation counts: {counts}")

    holding = conn.execute(
        "SELECT id,name,short_name,status FROM holdings"
    ).fetchone()
    check(holding is not None, "holding missing")
    check(
        (holding["name"], holding["short_name"], holding["status"])
        == (spec["holding"]["name"], "", "active"),
        f"holding mismatch: {dict(holding)}",
    )

    organization = conn.execute(
        "SELECT id,holding_id,name,short_name,status FROM organizations"
    ).fetchone()
    check(organization is not None, "organization missing")
    check(
        organization["holding_id"] == holding["id"]
        and organization["name"] == spec["organization"]["name"]
        and organization["short_name"] == ""
        and organization["status"] == "active",
        f"organization mismatch: {dict(organization)}",
    )

    cluster = conn.execute(
        "SELECT id,organization_id,name,description,status FROM clusters"
    ).fetchone()
    check(cluster is not None, "cluster missing")
    check(
        cluster["organization_id"] == organization["id"]
        and cluster["name"] == spec["cluster"]["name"]
        and cluster["description"] == ""
        and cluster["status"] == "active",
        f"cluster mismatch: {dict(cluster)}",
    )

    clinic = conn.execute(
        """
        SELECT id,organization_id,cluster_id,name,short_name,region,city,address,timezone,status
        FROM clinics
        """
    ).fetchone()
    check(clinic is not None, "clinic missing")
    check(
        clinic["organization_id"] == organization["id"]
        and clinic["cluster_id"] == cluster["id"]
        and clinic["name"] == spec["clinic"]["name"]
        and clinic["short_name"] == ""
        and clinic["region"] == ""
        and clinic["city"] == ""
        and clinic["address"] == ""
        and clinic["timezone"] == spec["clinic"]["timezone"]
        and clinic["status"] == "active",
        f"clinic mismatch: {dict(clinic)}",
    )

    verify_foundation_migration_row(conn)

    sequences = _sqlite_sequences(conn)
    expected_seed_sequences = {
        "holdings": holding["id"],
        "organizations": organization["id"],
        "clusters": cluster["id"],
        "clinics": clinic["id"],
    }
    for table, expected_seq in expected_seed_sequences.items():
        check(
            sequences.get(table) == expected_seq,
            f"unexpected AUTOINCREMENT sequence for {table}: "
            f"{sequences.get(table)} != {expected_seq}",
        )

    if baseline is not None and strict_unchanged:
        baseline_sequences = baseline["sqlite_sequences"]
        for name, seq in baseline_sequences.items():
            if name not in SEED_WRITTEN_TABLES:
                check(
                    sequences.get(name) == seq,
                    f"seed changed unrelated sqlite_sequence: {name}",
                )
        unexpected_sequences = (
            set(sequences)
            - set(baseline_sequences)
            - set(expected_seed_sequences)
        )
        check(
            not unexpected_sequences,
            f"seed created unexpected sqlite_sequence entries: "
            f"{sorted(unexpected_sequences)}",
        )
        check(
            _row_counts(conn, UNCHANGED_TABLES)
            == {table: baseline["row_counts"][table] for table in sorted(UNCHANGED_TABLES)},
            "seed changed row counts outside target tables",
        )
        check(
            _table_content_hashes(conn, UNCHANGED_TABLES)
            == {
                table: baseline["content_hashes"][table]
                for table in sorted(UNCHANGED_TABLES)
            },
            "seed changed content outside target tables",
        )

    return {
        "holding_id": holding["id"],
        "organization_id": organization["id"],
        "cluster_id": cluster["id"],
        "clinic_id": clinic["id"],
        "timezone": clinic["timezone"],
    }


def _db_sidecar_targets(db_path):
    target = Path(db_path).resolve()
    return {
        str(target),
        str(Path(str(target) + "-wal")),
        str(Path(str(target) + "-shm")),
        str(Path(str(target) + "-journal")),
    }


def find_open_db_handles(db_path, proc_root=Path("/proc")):
    targets = _db_sidecar_targets(db_path)
    own_pid = os.getpid()
    found = []
    unknown = []

    for proc_dir in Path(proc_root).iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == own_pid:
            continue
        fd_dir = proc_dir / "fd"
        try:
            entries = list(fd_dir.iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            unknown.append(pid)
            continue

        for fd in entries:
            try:
                link = os.readlink(fd)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if link.endswith(" (deleted)"):
                link = link[:-10]
            try:
                normalized = str(Path(link).resolve(strict=False))
            except (OSError, RuntimeError):
                normalized = link
            if normalized in targets:
                found.append({"pid": pid, "fd": fd.name, "path": normalized})

    return found, sorted(set(unknown))


def assert_database_quiescent(db_path, proc_root=Path("/proc")):
    handles, unknown = find_open_db_handles(db_path, proc_root=proc_root)
    check(not unknown, f"cannot prove DB quiescence; unreadable /proc fd dirs: {unknown}")
    check(not handles, f"database/sidecar still open by other processes: {handles}")


def _download(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AZ-BAZE-Structure-Controlled-Seed/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def download_sources(target_dir):
    root = Path(target_dir)
    migration_dir = root / "migrations"
    migration_dir.mkdir(parents=True, exist_ok=True)

    items = [
        ("db_migrations.py", RUNNER_BLOB, root / "db_migrations.py"),
        (
            "migrations/v20260920_001_structure_foundation.py",
            MIGRATION_BLOB,
            migration_dir / "v20260920_001_structure_foundation.py",
        ),
        ("structure_seed.py", SEED_HELPER_BLOB, root / "structure_seed.py"),
    ]

    for relative, expected_blob, destination in items:
        url = (
            "https://raw.githubusercontent.com/"
            "pat16071975-ship-it/architecture-health-site/"
            f"{SOURCE_COMMIT}/az-baze-auth/{relative}"
        )
        data = _download(url)
        actual = git_blob_sha(data)
        check(
            actual == expected_blob,
            f"source blob mismatch for {relative}: {actual} != {expected_blob}",
        )
        compile(data, str(destination), "exec")
        destination.write_bytes(data)

    return root / "db_migrations.py", migration_dir, root / "structure_seed.py"


def load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    check(spec is not None and spec.loader is not None, f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_migration_applied(runner, db_path, migration_dir):
    status_rows = runner.migration_status(db_path, migration_dir)
    check(
        status_rows
        == [{"version": FOUNDATION_VERSION, "name": FOUNDATION_NAME, "state": "applied"}],
        f"foundation migration status is not applied: {status_rows}",
    )


def _run(command, check_result=True):
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    if check_result and process.returncode != 0:
        raise ControlledSeedError(
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
    raise ControlledSeedError(f"{SERVICE} failed health check after start")


def restore_backup(db_path, backup_path, baseline, original_stat, expected_sha256):
    target = Path(db_path)
    backup = Path(backup_path)
    check(backup.exists(), f"rollback backup missing: {backup}")
    check(
        sha256_file(backup) == expected_sha256,
        "rollback backup SHA-256 mismatch; restore refused",
    )
    verify_exact_baseline(backup, baseline)

    if service_is_active():
        stop_service()
    assert_database_quiescent(target)

    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    assert_database_quiescent(target)

    restore_tmp = target.with_name(target.name + ".seed-rollback.tmp")
    shutil.copy2(backup, restore_tmp)
    os.chown(restore_tmp, original_stat.st_uid, original_stat.st_gid)
    os.chmod(restore_tmp, stat.S_IMODE(original_stat.st_mode))
    os.replace(restore_tmp, target)

    verify_exact_baseline(target, baseline)
    start_service()


def controlled_preflight(timezone_name, db_path=DB_PATH):
    spec = seed_spec(timezone_name)
    check(socket.gethostname() == EXPECTED_HOSTNAME, f"hostname must be {EXPECTED_HOSTNAME}")
    configured = configured_database_path()
    check(
        configured == Path(db_path).resolve(),
        f"service database path mismatch: configured={configured} controlled={Path(db_path).resolve()}",
    )
    check(service_is_active(), f"{SERVICE} is not active")
    check(local_health_ok(), "local health check failed")

    with tempfile.TemporaryDirectory(prefix="az-structure-seed-preflight-") as temp:
        runner_path, migration_dir, _seed_path = download_sources(temp)
        migration_path = migration_dir / "v20260920_001_structure_foundation.py"
        runner = load_module(runner_path, "az_seed_preflight_db_migrations")
        verify_migration_applied(runner, db_path, migration_dir)
        expected_schema = expected_foundation_schema(migration_path)
        baseline = capture_baseline(
            db_path,
            expected_schema=expected_schema,
            migration_path=migration_path,
        )

    return spec, baseline


def apply_seed_transaction(db_path, spec, baseline, seed_helper):
    conn = _connect_rw(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = seed_helper.seed_initial_structure(
                conn,
                holding_name=spec["holding"]["name"],
                organization_name=spec["organization"]["name"],
                cluster_name=spec["cluster"]["name"],
                clinic_name=spec["clinic"]["name"],
                timezone_name=spec["clinic"]["timezone"],
                holding_short_name="",
                organization_short_name="",
                clinic_short_name="",
                region="",
                city="",
                address="",
            )
            verify_exact_foundation_schema(
                conn,
                baseline["expected_foundation_schema"],
            )
            if baseline.get("migration_checksum") is not None:
                rows = conn.execute(
                    "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                check(
                    len(rows) == 1
                    and rows[0]["version"] == FOUNDATION_VERSION
                    and rows[0]["name"] == FOUNDATION_NAME
                    and rows[0]["checksum"] == baseline["migration_checksum"],
                    "foundation migration history/checksum changed during seed",
                )
            verified = verify_seeded_state(
                conn,
                spec,
                baseline=baseline,
                strict_unchanged=True,
            )
            check(result == {
                "holding_id": verified["holding_id"],
                "organization_id": verified["organization_id"],
                "cluster_id": verified["cluster_id"],
                "clinic_id": verified["clinic_id"],
            }, f"seed helper result mismatch: {result} vs {verified}")
            conn.commit()
            return verified
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def controlled_seed(timezone_name, db_path=DB_PATH):
    check(os.geteuid() == 0, "seed apply must run as root")
    spec, _ = controlled_preflight(timezone_name, db_path)
    precheck_backup_capacity(db_path, BACKUP_ROOT)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / f"structure-seed-{timestamp}"
    backup_path = backup_dir / "auth-before-structure-seed.db"
    baseline_path = backup_dir / "baseline.json"
    backup_meta_path = backup_dir / "backup-meta.json"
    seed_spec_path = backup_dir / "seed-spec.json"

    original_stat = Path(db_path).stat()
    service_stopped = False
    backup_ready = False
    backup_sha256 = None
    baseline = None

    with tempfile.TemporaryDirectory(prefix="az-structure-seed-") as temp:
        runner_path, migration_dir, seed_path = download_sources(temp)
        migration_path = migration_dir / "v20260920_001_structure_foundation.py"
        runner = load_module(runner_path, "az_seed_db_migrations")
        seed_helper = load_module(seed_path, "az_seed_helper")
        verify_migration_applied(runner, db_path, migration_dir)
        expected_schema = expected_foundation_schema(migration_path)

        try:
            step("QUIESCE SERVICE")
            stop_service()
            service_stopped = True
            assert_database_quiescent(db_path)

            step("CAPTURE QUIESCENT BASELINE")
            verify_migration_applied(runner, db_path, migration_dir)
            baseline = capture_baseline(
                db_path,
                expected_schema=expected_schema,
                migration_path=migration_path,
            )
            assert_database_quiescent(db_path)

            step("BACKUP + VERIFY")
            backup_meta = backup_database(db_path, backup_path, baseline)
            backup_ready = True
            backup_sha256 = backup_meta["sha256"]

            baseline_path.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            backup_meta_path.write_text(
                json.dumps(backup_meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            seed_spec_path.write_text(
                json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for path in (baseline_path, backup_meta_path, seed_spec_path):
                os.chmod(path, 0o600)

            check(sha256_file(backup_path) == backup_sha256, "backup sha256 changed")
            assert_database_quiescent(db_path)

            step("APPLY CURRENT AZ STRUCTURE SEED")
            seeded = apply_seed_transaction(db_path, spec, baseline, seed_helper)

            step("POSTSEED DATABASE VERIFICATION")
            conn = _connect_ro(db_path)
            try:
                verify_exact_foundation_schema(
                    conn,
                    baseline["expected_foundation_schema"],
                )
                rows = conn.execute(
                    "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                check(
                    len(rows) == 1
                    and rows[0]["version"] == FOUNDATION_VERSION
                    and rows[0]["name"] == FOUNDATION_NAME
                    and rows[0]["checksum"] == baseline["migration_checksum"],
                    "foundation migration history/checksum changed after seed",
                )
                verify_seeded_state(
                    conn,
                    spec,
                    baseline=baseline,
                    strict_unchanged=True,
                )
            finally:
                conn.close()
            assert_database_quiescent(db_path)

        except Exception:
            if backup_ready and baseline is not None and backup_sha256 is not None:
                print("\nCONTROLLED SEED FAILED: automatic rollback starting", file=sys.stderr)
                try:
                    restore_backup(
                        db_path,
                        backup_path,
                        baseline,
                        original_stat,
                        backup_sha256,
                    )
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
                    print(
                        f"SERVICE RESTART AFTER PRE-SEED FAILURE FAILED: {restart_exc}",
                        file=sys.stderr,
                    )
            raise

        step("RESTART + HEALTH")
        try:
            start_service()
            service_stopped = False
        except Exception as start_exc:
            try:
                if service_is_active():
                    stop_service()
                    service_stopped = True
            finally:
                raise ControlledSeedError(
                    "post-seed service start/health failed; automatic DB restore is "
                    "prohibited after a start attempt. Service was stopped when possible; "
                    f"verified backup retained at {backup_path}"
                ) from start_exc

        step("FINAL READ-ONLY VERIFICATION")
        try:
            verify_migration_applied(runner, db_path, migration_dir)
            conn = _connect_ro(db_path)
            try:
                verify_seeded_state(conn, spec)
            finally:
                conn.close()
        except Exception as runtime_exc:
            raise ControlledSeedError(
                "post-restart diagnostics failed after traffic may have resumed; "
                "automatic restore is prohibited. Preserve current DB and backup for "
                "manual incident decision."
            ) from runtime_exc

    print("\nCONTROLLED CURRENT AZ STRUCTURE SEED: PASS")
    print(f"SEED_SPEC_SHA256={seed_spec_digest(spec)}")
    print(f"BACKUP={backup_path}")
    print(f"BACKUP_SHA256={sha256_file(backup_path)}")
    print(f"HOLDING_ID={seeded['holding_id']}")
    print(f"ORGANIZATION_ID={seeded['organization_id']}")
    print(f"CLUSTER_ID={seeded['cluster_id']}")
    print(f"CLINIC_ID={seeded['clinic_id']}")
    print(f"CLINIC_TIMEZONE={seeded['timezone']}")
    print("DIRECTIONS_CREATED=0")
    print("LIVE_APP_CODE=NOT_DEPLOYED")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Controlled seed of current Architecture Health structure"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Read-only checks only")
    mode.add_argument("--apply", action="store_true", help="Backup + controlled seed")
    parser.add_argument("--timezone", required=True, help="Clinic IANA timezone, e.g. Europe/Moscow")
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    spec = seed_spec(args.timezone)

    if args.preflight:
        step("READ-ONLY SEED PREFLIGHT")
        controlled_preflight(args.timezone, DB_PATH)
        print(f"SEED_SPEC_SHA256={seed_spec_digest(spec)}")
        print(f"APPLY_CONFIRMATION={required_confirmation(spec)}")
        print(f"CLINIC_TIMEZONE={spec['clinic']['timezone']}")
        print("FOUNDATION_TABLES=EMPTY")
        print("FOUNDATION_MIGRATION=APPLIED")
        print("CONTROLLED SEED PREFLIGHT: PASS")
        return 0

    check(
        args.confirm == required_confirmation(spec),
        f"seed confirmation mismatch; required: {required_confirmation(spec)}",
    )
    return controlled_seed(args.timezone, DB_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
