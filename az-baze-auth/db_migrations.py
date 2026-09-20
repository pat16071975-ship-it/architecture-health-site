import argparse
import hashlib
import importlib.util
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MIGRATIONS_DIR = BASE_DIR / "migrations"
MIGRATION_FILE_RE = re.compile(r"^v(?P<version>\d{8}_\d{3})_(?P<name>[a-z0-9_]+)\.py$")


AZBAZE_DB_ANCHORS = {
    "users": {"id", "email", "password_hash", "active", "created_at", "updated_at"},
    "permissions": {"user_id", "section"},
    "audit_log": {"id", "action", "created_at"},
}

class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    checksum: str
    upgrade: object


def _checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _existing_db_path(db_path):
    path = Path(db_path).expanduser()
    if not path.exists():
        raise MigrationError(f"Database does not exist: {path}")
    if not path.is_file():
        raise MigrationError(f"Database path is not a file: {path}")
    return path.resolve()


def _connect_existing(db_path, mode):
    path = _existing_db_path(db_path)
    uri = path.as_uri() + f"?mode={mode}"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise MigrationError(f"Cannot open database in mode={mode}: {path}") from exc


def _validate_az_baze_database(conn):
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = sorted(set(AZBAZE_DB_ANCHORS) - tables)
        if missing_tables:
            raise MigrationError(
                "Database is not recognized as AZ-BAZE; missing anchor tables: "
                + ", ".join(missing_tables)
            )

        for table, required_columns in AZBAZE_DB_ANCHORS.items():
            columns = {
                row["name"]
                for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                raise MigrationError(
                    f"Database is not recognized as AZ-BAZE; {table} is missing columns: "
                    + ", ".join(missing_columns)
                )
    except sqlite3.DatabaseError as exc:
        raise MigrationError("Database is not a readable AZ-BAZE SQLite database") from exc


def _preflight_existing_database(db_path):
    path = _existing_db_path(db_path)
    conn = _connect_existing(path, "ro")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        _validate_az_baze_database(conn)
    finally:
        conn.close()
    return path


def discover_migrations(migrations_dir=None):
    root = Path(migrations_dir or DEFAULT_MIGRATIONS_DIR)
    if not root.exists() or not root.is_dir():
        raise MigrationError(f"Migrations directory does not exist: {root}")

    migrations = []
    for path in sorted(root.glob("v*.py")):
        match = MIGRATION_FILE_RE.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Malformed migration filename: {path.name}")

        module_name = f"az_baze_migration_{match.group('version')}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if not spec or not spec.loader:
            raise MigrationError(f"Cannot load migration: {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        version = str(getattr(module, "VERSION", ""))
        name = str(getattr(module, "NAME", ""))
        upgrade = getattr(module, "upgrade", None)
        if version != match.group("version") or name != match.group("name") or not callable(upgrade):
            raise MigrationError(f"Migration metadata mismatch: {path.name}")

        migrations.append(Migration(version, name, path, _checksum(path), upgrade))

    versions = [item.version for item in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration version")
    return migrations


def _history_table_exists(conn):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    )


def _ensure_history_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _validate_history(conn, migrations):
    if not _history_table_exists(conn):
        return {}

    catalogue = {migration.version: migration for migration in migrations}
    catalogue_versions = [migration.version for migration in migrations]
    rows = list(
        conn.execute(
            "SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version"
        )
    )
    recorded = {row["version"]: row for row in rows}
    recorded_versions = [row["version"] for row in rows]

    missing = sorted(set(recorded) - set(catalogue))
    if missing:
        raise MigrationError(
            "Applied migration missing from repository: " + ", ".join(missing)
        )

    expected_prefix = catalogue_versions[: len(recorded_versions)]
    if recorded_versions != expected_prefix:
        raise MigrationError(
            "Migration history is not a contiguous catalogue prefix; "
            f"database={recorded_versions} expected={expected_prefix}"
        )

    for version, row in recorded.items():
        migration = catalogue[version]
        if row["name"] != migration.name:
            raise MigrationError(
                f"Applied migration name changed: {version}; "
                f"database={row['name']} repository={migration.name}"
            )
        if row["checksum"] != migration.checksum:
            raise MigrationError(
                f"Applied migration checksum changed: {version}_{migration.name}"
            )

    return recorded


def apply_migrations(db_path, migrations_dir=None):
    migrations = discover_migrations(migrations_dir)
    path = _preflight_existing_database(db_path)
    conn = _connect_existing(path, "rw")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # Recheck identity on the writable connection before any write transaction.
        _validate_az_baze_database(conn)
        # Validate the existing history before opening any write transaction.
        recorded = _validate_history(conn, migrations)
        applied = []
        skipped = []

        for migration in migrations:
            if migration.version in recorded:
                skipped.append(migration.version)
                continue

            conn.execute("BEGIN IMMEDIATE")
            try:
                # The history table is created inside the same transaction as the
                # first migration. A failed first migration therefore rolls back
                # both its schema changes and the history table creation.
                _ensure_history_table(conn)

                # Revalidate while holding the write lock so a concurrent runner
                # cannot make catalogue/history drift invisible.
                current = _validate_history(conn, migrations)
                if migration.version in current:
                    conn.rollback()
                    recorded = current
                    skipped.append(migration.version)
                    continue

                migration.upgrade(conn)
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version,name,checksum,applied_at)
                    VALUES(?,?,?,?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            applied.append(migration.version)
            recorded[migration.version] = {
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
            }

        return {"applied": applied, "already_applied": skipped}
    finally:
        conn.close()


def migration_status(db_path, migrations_dir=None):
    migrations = discover_migrations(migrations_dir)
    conn = _connect_existing(db_path, "ro")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    try:
        _validate_az_baze_database(conn)
        recorded = _validate_history(conn, migrations)
        result = []
        for migration in migrations:
            result.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "state": "applied" if migration.version in recorded else "pending",
                }
            )
        return result
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="AZ-BAZE versioned SQLite migrations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Read-only migration status")
    status_parser.add_argument("--db", required=True)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply pending migrations to an existing SQLite database",
    )
    apply_parser.add_argument("--db", required=True)

    args = parser.parse_args(argv)
    if args.command == "status":
        for item in migration_status(args.db):
            print(f"{item['version']} {item['name']}: {item['state']}")
        return 0

    result = apply_migrations(args.db)
    print("applied:", ",".join(result["applied"]) or "-")
    print("already_applied:", ",".join(result["already_applied"]) or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
