import argparse
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STRUCTURE_TABLES = (
    "holdings",
    "organizations",
    "clusters",
    "clinics",
    "directions",
    "clinic_directions",
)


class SeedError(RuntimeError):
    pass


def _required(value, label):
    text = str(value or "").strip()
    if not text:
        raise SeedError(f"{label} is required")
    return text


def _valid_timezone(value):
    timezone_name = _required(value, "timezone")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SeedError(f"Unknown IANA timezone: {timezone_name}") from exc
    return timezone_name


def seed_initial_structure(
    conn,
    *,
    holding_name,
    organization_name,
    cluster_name,
    clinic_name,
    timezone_name,
    holding_short_name="",
    organization_short_name="",
    clinic_short_name="",
    region="",
    city="",
    address="",
):
    holding_name = _required(holding_name, "holding_name")
    organization_name = _required(organization_name, "organization_name")
    cluster_name = _required(cluster_name, "cluster_name")
    clinic_name = _required(clinic_name, "clinic_name")
    timezone_name = _valid_timezone(timezone_name)

    missing = [
        table
        for table in STRUCTURE_TABLES
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    ]
    if missing:
        raise SeedError("Structure migration is not applied: " + ", ".join(missing))

    nonempty = [
        table
        for table in ("holdings", "organizations", "clusters", "clinics")
        if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
    ]
    if nonempty:
        raise SeedError(
            "Initial structure seed requires empty structure tables: " + ", ".join(nonempty)
        )

    cur = conn.execute(
        "INSERT INTO holdings(name,short_name,status) VALUES(?,?,'active')",
        (holding_name, str(holding_short_name or "").strip()),
    )
    holding_id = cur.lastrowid

    cur = conn.execute(
        """
        INSERT INTO organizations(holding_id,name,short_name,status)
        VALUES(?,?,?,'active')
        """,
        (holding_id, organization_name, str(organization_short_name or "").strip()),
    )
    organization_id = cur.lastrowid

    cur = conn.execute(
        """
        INSERT INTO clusters(organization_id,name,description,status)
        VALUES(?,?,?,'active')
        """,
        (organization_id, cluster_name, ""),
    )
    cluster_id = cur.lastrowid

    cur = conn.execute(
        """
        INSERT INTO clinics(
            organization_id,cluster_id,name,short_name,region,city,address,timezone,status
        ) VALUES(?,?,?,?,?,?,?,?,'active')
        """,
        (
            organization_id,
            cluster_id,
            clinic_name,
            str(clinic_short_name or "").strip(),
            str(region or "").strip(),
            str(city or "").strip(),
            str(address or "").strip(),
            timezone_name,
        ),
    )
    clinic_id = cur.lastrowid

    return {
        "holding_id": holding_id,
        "organization_id": organization_id,
        "cluster_id": cluster_id,
        "clinic_id": clinic_id,
    }


def seed_database(db_path, **kwargs):
    conn = sqlite3.connect(Path(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = seed_initial_structure(conn, **kwargs)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed one clean AZ-BAZE structure contour")
    parser.add_argument("--db", required=True)
    parser.add_argument("--holding", required=True)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--clinic", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--region", default="")
    parser.add_argument("--city", default="")
    parser.add_argument("--address", default="")
    args = parser.parse_args(argv)

    result = seed_database(
        args.db,
        holding_name=args.holding,
        organization_name=args.organization,
        cluster_name=args.cluster,
        clinic_name=args.clinic,
        timezone_name=args.timezone,
        region=args.region,
        city=args.city,
        address=args.address,
    )
    print(
        "seeded:",
        f"holding={result['holding_id']}",
        f"organization={result['organization_id']}",
        f"cluster={result['cluster_id']}",
        f"clinic={result['clinic_id']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
