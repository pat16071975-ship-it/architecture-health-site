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


def discover_migrations(migrations_dir=None):
    root = Path(migrations_dir or DEFAULT_MIGRATIONS_DIR)
    migrations = []
    for path in sorted(root.glob("v*.py")):
        match = MIGRATION_FILE_RE.fullmatch(path.name)
        if not match:
            continue
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


def apply_migrations(db_path, migrations_dir=None):
    migrations = discover_migrations(migrations_dir)
    path = Path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_history_table(conn)
        conn.commit()
        applied = []
        skipped = []

        for migration in migrations:
            row = conn.execute(
                "SELECT name,checksum FROM schema_migrations WHERE version=?",
                (migration.version,),
            ).fetchone()
            if row:
                if row["name"] != migration.name or row["checksum"] != migration.checksum:
                    raise MigrationError(
                        f"Applied migration changed: {migration.version}_{migration.name}"
                    )
                skipped.append(migration.version)
                continue

            conn.execute("BEGIN IMMEDIATE")
            try:
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

        return {"applied": applied, "already_applied": skipped}
    finally:
        conn.close()


def migration_status(db_path, migrations_dir=None):
    migrations = discover_migrations(migrations_dir)
    path = Path(db_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        has_history = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        recorded = {}
        if has_history:
            recorded = {
                row["version"]: row
                for row in conn.execute(
                    "SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version"
                )
            }

        result = []
        for migration in migrations:
            row = recorded.get(migration.version)
            state = "pending"
            if row:
                state = (
                    "applied"
                    if row["name"] == migration.name and row["checksum"] == migration.checksum
                    else "checksum_mismatch"
                )
            result.append(
                {"version": migration.version, "name": migration.name, "state": state}
            )
        return result
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="AZ-BAZE versioned SQLite migrations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Read-only migration status")
    status_parser.add_argument("--db", required=True)

    apply_parser = subparsers.add_parser("apply", help="Apply pending migrations")
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
