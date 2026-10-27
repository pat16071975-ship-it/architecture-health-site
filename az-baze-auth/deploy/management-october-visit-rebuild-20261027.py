#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.request

EXPECTED_HOST = "az-server"
DB = Path("/var/lib/az-baze/auth.db")
APP_ROOT = Path("/opt/az-baze-auth")
BACKUP_ROOT = Path("/var/lib/az-baze/backups")
MONTH = "2026-10"
SERVICE = "az-baze-auth"
HEALTH_URL = "http://127.0.0.1:8001/health"
CONFIRM_TOKEN = "MANAGEMENT-OCTOBER-VISITS-20261027"

FILES = {
    "paid_services.py": {
        "path": APP_ROOT / "paid_services.py",
        "base": "76c1a33de8a711f85139a21aa5f66a48ea96910f",
        "target": "54ed8eb74d27003a31f2c52dceb25b7ce4ac99fb",
    },
    "daily_upload.py": {
        "path": APP_ROOT / "daily_upload.py",
        "base": "0ceb17ceb4b0f3f1feec20e711a22a692f2a3d06",
        "target": "8c4e456edf241ecb75e819982385f992cbe4931b",
    },
    "templates/uploads.html": {
        "path": APP_ROOT / "templates" / "uploads.html",
        "base": "d0ecaec2d569c61bec113e30fde63de90ec3b6fe",
        "target": "5a4273f62a120902b2785d4de6a238c050724d65",
    },
}


def git_blob(path: Path) -> str:
    raw = path.read_bytes()
    head = f"blob {len(raw)}\0".encode()
    return hashlib.sha1(head + raw).hexdigest()


def source_state():
    states = {}
    for name, spec in FILES.items():
        if not spec["path"].is_file():
            raise RuntimeError(f"SOURCE_FILE_MISSING:{name}")
        blob = git_blob(spec["path"])
        if blob == spec["target"]:
            state = "TARGET"
        elif blob == spec["base"]:
            state = "BASE"
        else:
            state = "UNKNOWN"
        states[name] = {"blob": blob, "state": state}
    unique = {v["state"] for v in states.values()}
    if unique == {"TARGET"}:
        overall = "TARGET"
    elif unique == {"BASE"}:
        overall = "BASE"
    elif "UNKNOWN" in unique:
        overall = "UNKNOWN"
    else:
        overall = "MIXED"
    return overall, states


def connect_ro(path: Path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def db_integrity(conn):
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "")


def clinical_rows(conn):
    rows = conn.execute(
        """
        SELECT data_date,completed_sha256,services_sha256,revision,normalized_json
        FROM daily_uploads
        WHERE substr(data_date,1,7)=?
        ORDER BY data_date
        """,
        (MONTH,),
    ).fetchall()
    if not rows:
        raise RuntimeError("NO_OCTOBER_DAILY_UPLOADS")
    return rows


def fingerprint(rows):
    state = []
    for row in rows:
        normalized = str(row["normalized_json"] or "")
        state.append(
            [
                str(row["data_date"]),
                str(row["completed_sha256"]),
                str(row["services_sha256"]),
                int(row["revision"] or 0),
                hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            ]
        )
    return hashlib.sha256(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def num_int(value, label):
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise RuntimeError(f"NON_NUMERIC:{label}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"NON_NUMERIC:{label}")
    if not number.is_integer():
        raise RuntimeError(f"NON_INTEGER:{label}")
    return int(number)


def load_payload(row):
    try:
        value = json.loads(row["payload"])
    except (TypeError, ValueError):
        raise RuntimeError(f"BAD_REPORT_JSON:{row['date']}")
    if not isinstance(value, dict):
        raise RuntimeError(f"BAD_REPORT_OBJECT:{row['date']}")
    return value


def proposed_totals(conn, rows):
    first_date = str(rows[0]["data_date"])
    baseline_row = conn.execute(
        """
        SELECT date,payload
        FROM report_data
        WHERE date < ? AND substr(date,1,7)=?
        ORDER BY date DESC
        LIMIT 1
        """,
        (first_date, MONTH),
    ).fetchone()

    primary = repeat = 0
    baseline_date = None
    if baseline_row:
        baseline_date = str(baseline_row["date"])
        baseline = load_payload(baseline_row)
        primary = num_int(baseline.get("primary"), f"{baseline_date}:primary")
        repeat = num_int(baseline.get("repeat"), f"{baseline_date}:repeat")

    proposed = {}
    for row in rows:
        data_date = str(row["data_date"])
        try:
            normalized = json.loads(row["normalized_json"])
        except (TypeError, ValueError):
            raise RuntimeError(f"BAD_NORMALIZED_JSON:{data_date}")
        if not isinstance(normalized, dict):
            raise RuntimeError(f"BAD_NORMALIZED_OBJECT:{data_date}")
        if str(normalized.get("data_date") or "") != data_date:
            raise RuntimeError(f"NORMALIZED_DATE_MISMATCH:{data_date}")
        overall = (normalized.get("overall") or {}).get(data_date, {}) or {}
        primary += num_int(overall.get("Первичные"), f"{data_date}:source_primary")
        repeat += num_int(overall.get("Повторные"), f"{data_date}:source_repeat")
        repeat += num_int(overall.get("Отконсультированные"), f"{data_date}:source_consulted")
        proposed[data_date] = {"primary": primary, "repeat": repeat}

    return baseline_date, proposed


def build_plan(conn):
    rows = clinical_rows(conn)
    fp = fingerprint(rows)
    baseline_date, proposed = proposed_totals(conn, rows)
    changes = []
    all_rows = []

    for data_date, target in proposed.items():
        row = conn.execute(
            "SELECT date,payload,updated_at FROM report_data WHERE date=?",
            (data_date,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"REPORT_ROW_MISSING:{data_date}")
        payload = load_payload(row)
        before_primary = num_int(payload.get("primary"), f"{data_date}:report_primary")
        before_repeat = num_int(payload.get("repeat"), f"{data_date}:report_repeat")
        after_primary = int(target["primary"])
        after_repeat = int(target["repeat"])

        if after_primary < before_primary:
            raise RuntimeError(
                f"REFUSE_PRIMARY_DECREASE:{data_date}:{before_primary}->{after_primary}"
            )
        if after_repeat < before_repeat:
            raise RuntimeError(
                f"REFUSE_REPEAT_DECREASE:{data_date}:{before_repeat}->{after_repeat}"
            )

        dent_primary = num_int(payload.get("dentPrimary"), f"{data_date}:dentPrimary")
        clinic_primary = num_int(payload.get("clinicPrimary"), f"{data_date}:clinicPrimary")
        dent_repeat = num_int(payload.get("dentRepeat"), f"{data_date}:dentRepeat")
        clinic_repeat = num_int(payload.get("clinicRepeat"), f"{data_date}:clinicRepeat")
        reported_primary = dent_primary + clinic_primary
        reported_repeat = dent_repeat + clinic_repeat

        control = dict(payload.get("_uploadControl") or {})
        control.update(
            {
                "sourcePrimary": after_primary,
                "sourceRepeat": after_repeat,
                "reportedPrimary": reported_primary,
                "reportedRepeat": reported_repeat,
                "unassignedPrimary": max(0, after_primary - reported_primary),
                "unassignedRepeat": max(0, after_repeat - reported_repeat),
            }
        )

        entry = {
            "date": data_date,
            "beforePrimary": before_primary,
            "afterPrimary": after_primary,
            "beforeRepeat": before_repeat,
            "afterRepeat": after_repeat,
            "reportedPrimary": reported_primary,
            "reportedRepeat": reported_repeat,
            "unassignedPrimary": control["unassignedPrimary"],
            "unassignedRepeat": control["unassignedRepeat"],
            "control": control,
        }
        all_rows.append(entry)
        if before_primary != after_primary or before_repeat != after_repeat:
            changes.append(entry)

    return {
        "fingerprint": fp,
        "baselineDate": baseline_date,
        "firstDate": str(rows[0]["data_date"]),
        "lastDate": str(rows[-1]["data_date"]),
        "changes": changes,
        "rows": all_rows,
    }


def print_plan(plan):
    print(f"OCTOBER_CLINICAL_FINGERPRINT={plan['fingerprint']}")
    print(f"CLINICAL_RANGE={plan['firstDate']}..{plan['lastDate']}")
    print(f"BASELINE_DATE={plan['baselineDate'] or 'NONE'}")
    print(f"CHANGED_ROWS={len(plan['changes'])}")
    print(
        "PRIMARY_DELTA_TOTAL="
        + str(sum(x["afterPrimary"] - x["beforePrimary"] for x in plan["changes"]))
    )
    print(
        "REPEAT_DELTA_TOTAL="
        + str(sum(x["afterRepeat"] - x["beforeRepeat"] for x in plan["changes"]))
    )
    for row in plan["changes"]:
        print("CHANGE=" + json.dumps(row, ensure_ascii=False, sort_keys=True))

    by_date = {row["date"]: row for row in plan["rows"]}
    for data_date in ("2026-10-25", "2026-10-26"):
        if data_date in by_date:
            print(
                "CONTROL_" + data_date.replace("-", "_") + "="
                + json.dumps(by_date[data_date], ensure_ascii=False, sort_keys=True)
            )


def health_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:
            return response.read().decode().strip() == "ok"
    except Exception:
        return False


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_backup():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = BACKUP_ROOT / f"management-october-visits-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    os.chmod(folder, 0o700)
    target = folder / "auth.db"
    src = connect_ro(DB)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        src.close()
        dst.close()
    check = connect_ro(target)
    try:
        if db_integrity(check) != "ok":
            raise RuntimeError("BACKUP_INTEGRITY_FAIL")
    finally:
        check.close()
    return folder, target, sha256_file(target)


def restore_backup(backup_db: Path):
    subprocess.run(["systemctl", "stop", SERVICE], check=False)
    shutil.copy2(backup_db, DB)
    subprocess.run(["systemctl", "start", SERVICE], check=False)
    if not health_ok():
        raise RuntimeError("ROLLBACK_HEALTH_FAIL")


def apply_plan(expected_fingerprint):
    backup_folder = backup_db = backup_sha = None
    committed = False
    try:
        backup_folder, backup_db, backup_sha = make_backup()
        print("BACKUP=PASS")
        print(f"BACKUP_PATH={backup_folder}")
        print(f"BACKUP_SHA256={backup_sha}")

        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            plan = build_plan(conn)
            if plan["fingerprint"] != expected_fingerprint:
                raise RuntimeError(
                    "FINGERPRINT_CHANGED:"
                    + plan["fingerprint"]
                    + ":expected:"
                    + expected_fingerprint
                )
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            changed = 0
            for entry in plan["changes"]:
                row = conn.execute(
                    "SELECT payload FROM report_data WHERE date=?",
                    (entry["date"],),
                ).fetchone()
                if not row:
                    raise RuntimeError(f"REPORT_ROW_DISAPPEARED:{entry['date']}")
                payload = json.loads(row["payload"])
                payload["primary"] = entry["afterPrimary"]
                payload["repeat"] = entry["afterRepeat"]
                payload["_uploadControl"] = entry["control"]
                cur = conn.execute(
                    """
                    UPDATE report_data
                    SET payload=?,updated_at=?
                    WHERE date=?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        now,
                        entry["date"],
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"UPDATE_COUNT_FAIL:{entry['date']}")
                changed += 1

            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
            ).fetchone():
                conn.execute(
                    """
                    INSERT INTO audit_log(
                        actor_user_id,action,target_user_id,details,created_at
                    ) VALUES(NULL,?,?,?,?)
                    """,
                    (
                        "management_clinical_visit_rebuild",
                        None,
                        (
                            f"month={MONTH}; changed_rows={changed}; "
                            f"fingerprint={expected_fingerprint}; "
                            "fields=primary,repeat,_uploadControl"
                        ),
                        now,
                    ),
                )
            conn.commit()
            committed = True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        verify = connect_ro(DB)
        try:
            if db_integrity(verify) != "ok":
                raise RuntimeError("POST_APPLY_INTEGRITY_FAIL")
            post = build_plan(verify)
            if post["fingerprint"] != expected_fingerprint:
                raise RuntimeError("POST_APPLY_FINGERPRINT_CHANGED")
            if post["changes"]:
                raise RuntimeError("POST_APPLY_RECALC_NOT_CLEAN")
        finally:
            verify.close()

        print("POST_APPLY_INTEGRITY=PASS")
        print("POST_APPLY_RECALC=PASS")
        print("DB_FIELDS_CHANGED=primary,repeat,_uploadControl")
        print("OTHER_REPORT_FIELDS_CHANGED=NONE")
        return backup_folder
    except Exception:
        if committed and backup_db and backup_db.exists():
            try:
                restore_backup(backup_db)
                print("ROLLBACK=PASS")
            except Exception as rollback_exc:
                print(f"ROLLBACK=FAIL:{type(rollback_exc).__name__}")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["--preflight", "--apply"])
    parser.add_argument("confirm", nargs="?")
    parser.add_argument("expected_fingerprint", nargs="?")
    args = parser.parse_args()

    if socket.gethostname().split(".")[0] != EXPECTED_HOST:
        raise RuntimeError("WRONG_HOST")
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    if not DB.is_file():
        raise RuntimeError("DB_MISSING")

    overall, states = source_state()
    print(f"SOURCE_STATE={overall}")
    for name, state in states.items():
        print(
            "SOURCE_FILE="
            + name
            + ";STATE="
            + state["state"]
            + ";BLOB="
            + state["blob"]
        )

    conn = connect_ro(DB)
    try:
        integrity = db_integrity(conn)
        print(f"DB_INTEGRITY={integrity}")
        if integrity != "ok":
            raise RuntimeError("DB_INTEGRITY_FAIL")
        plan = build_plan(conn)
    finally:
        conn.close()

    print_plan(plan)

    if args.mode == "--preflight":
        print("=== FINAL ===")
        print("MANAGEMENT_OCTOBER_VISIT_REBUILD_PREFLIGHT=PASS")
        print("LIVE_WRITES=NONE")
        print("APPLY_REQUIRES_SOURCE_STATE=TARGET")
        return

    if args.confirm != CONFIRM_TOKEN:
        raise RuntimeError("CONFIRMATION_FAIL")
    if not args.expected_fingerprint:
        raise RuntimeError("EXPECTED_FINGERPRINT_REQUIRED")
    if overall != "TARGET":
        raise RuntimeError("SOURCE_FIX_NOT_DEPLOYED")
    if args.expected_fingerprint != plan["fingerprint"]:
        raise RuntimeError("EXPECTED_FINGERPRINT_MISMATCH")
    if not health_ok():
        raise RuntimeError("HEALTH_PRE_FAIL")

    backup_folder = apply_plan(args.expected_fingerprint)

    if not health_ok():
        raise RuntimeError("HEALTH_POST_FAIL")

    print("=== FINAL ===")
    print("MANAGEMENT_OCTOBER_VISIT_REBUILD_APPLY=PASS")
    print(f"FINGERPRINT={args.expected_fingerprint}")
    print(f"BACKUP={backup_folder}")
    print("SERVICE=active")
    print("HEALTH=ok")
    print("DAILY_UPLOADS_CHANGED=NO")
    print("CASH_DATA_CHANGED=NO")
    print("PAID_DATA_CHANGED=NO")
    print("REPORT_FIELDS_CHANGED=primary,repeat,_uploadControl")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"STOP={type(exc).__name__}:{exc}")
        sys.exit(1)
