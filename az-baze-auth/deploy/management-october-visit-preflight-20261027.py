#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys

EXPECTED_HOST="az-server"
DB=Path("/var/lib/az-baze/auth.db")
MONTH="2026-10"

TARGET_FILES={
    "/opt/az-baze-auth/daily_upload.py":"c6291e649c0d40fbae457572349cd2f2967f14bc",
    "/opt/az-baze-auth/daily_upload_core.py":"d2a44fd700a3b0b03e6db64dc5936da25e7b7e39",
    "/opt/az-baze-auth/paid_services.py":"6aadd5b5e1464d027425d163b4b47fe718ddd589",
    "/opt/az-baze-auth/upload_reconcile.py":"5cad7baa20e2f8c77d7ab389d8137f7d5452db75",
    "/opt/az-baze-auth/templates/uploads.html":"63a7e261c71d8695649d7e8aa7d55e20edd0dd1c",
}

def git_blob(path):
    raw=Path(path).read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest()

def as_int(value,label):
    if value in (None,""):
        return 0
    try:
        n=float(value)
    except (TypeError,ValueError):
        raise RuntimeError(f"NON_NUMERIC:{label}")
    if not n.is_integer():
        raise RuntimeError(f"NON_INTEGER:{label}")
    return int(n)

def parse_json(raw,label):
    try:
        value=json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"BAD_JSON:{label}") from exc
    if not isinstance(value,dict):
        raise RuntimeError(f"BAD_OBJECT:{label}")
    return value

def fingerprint(rows):
    state=[]
    for row in rows:
        state.append([
            str(row["data_date"]),
            str(row["completed_sha256"]),
            str(row["services_sha256"]),
            int(row["revision"] or 0),
            hashlib.sha256(str(row["normalized_json"]).encode("utf-8")).hexdigest(),
        ])
    return hashlib.sha256(
        json.dumps(state,ensure_ascii=False,separators=(",",":")).encode()
    ).hexdigest()

def main():
    if os.geteuid()!=0:
        raise RuntimeError("ROOT_REQUIRED")
    if socket.gethostname().split(".")[0]!=EXPECTED_HOST:
        raise RuntimeError("WRONG_HOST")
    if not DB.is_file():
        raise RuntimeError("DB_MISSING")

    for path,expected in TARGET_FILES.items():
        p=Path(path)
        if not p.is_file():
            raise RuntimeError("SOURCE_FILE_MISSING:"+path)
        actual=git_blob(path)
        print(f"SOURCE_FILE={path};BLOB={actual}")
        if actual!=expected:
            raise RuntimeError(f"SOURCE_NOT_TARGET:{path}")

    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True)
    c.row_factory=sqlite3.Row
    try:
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        print("DB_INTEGRITY="+str(integrity))
        if integrity!="ok":
            raise RuntimeError("DB_INTEGRITY_FAIL")

        table=c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='clinical_visit_direction_overrides'"
        ).fetchone()
        if not table:
            raise RuntimeError("VISIT_DIRECTION_SCHEMA_MISSING")
        override_count=c.execute(
            "SELECT COUNT(*) FROM clinical_visit_direction_overrides"
        ).fetchone()[0]
        print("VISIT_DIRECTION_ROWS="+str(override_count))

        rows=c.execute(
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

        fp=fingerprint(rows)
        print("OCTOBER_CLINICAL_FINGERPRINT="+fp)
        first=str(rows[0]["data_date"])
        last=str(rows[-1]["data_date"])
        print(f"DAILY_UPLOAD_RANGE={first}..{last}")

        baseline_row=c.execute(
            """
            SELECT date,payload FROM report_data
            WHERE date<? AND substr(date,1,7)=?
            ORDER BY date DESC LIMIT 1
            """,
            (first,MONTH),
        ).fetchone()

        primary=repeat=0
        baseline_date="NONE"
        if baseline_row:
            baseline_date=str(baseline_row["date"])
            baseline=parse_json(baseline_row["payload"],"baseline")
            primary=as_int(baseline.get("primary"),baseline_date+":primary")
            repeat=as_int(baseline.get("repeat"),baseline_date+":repeat")
        print("BASELINE_DATE="+baseline_date)
        print("BASELINE_PRIMARY="+str(primary))
        print("BASELINE_REPEAT="+str(repeat))

        changes=[]
        controls=[]
        manual_by_date={}
        for row in c.execute(
            """
            SELECT data_date,direction,COUNT(*) AS n
            FROM clinical_visit_direction_overrides
            WHERE substr(data_date,1,7)=?
            GROUP BY data_date,direction
            ORDER BY data_date,direction
            """,
            (MONTH,),
        ):
            manual_by_date.setdefault(str(row["data_date"]),{})[str(row["direction"])]=int(row["n"])

        for row in rows:
            date=str(row["data_date"])
            normalized=parse_json(row["normalized_json"],"daily:"+date)
            overall=(normalized.get("overall") or {}).get(date,{}) or {}
            primary+=as_int(overall.get("Первичные"),date+":source_primary")
            repeat+=as_int(overall.get("Повторные"),date+":source_repeat")
            repeat+=as_int(overall.get("Отконсультированные"),date+":source_consulted")

            report_row=c.execute(
                "SELECT payload FROM report_data WHERE date=?",(date,)
            ).fetchone()
            if not report_row:
                raise RuntimeError("REPORT_ROW_MISSING:"+date)
            report=parse_json(report_row["payload"],"report:"+date)
            before_primary=as_int(report.get("primary"),date+":report_primary")
            before_repeat=as_int(report.get("repeat"),date+":report_repeat")

            control={
                "date":date,
                "beforePrimary":before_primary,
                "afterPrimary":primary,
                "beforeRepeat":before_repeat,
                "afterRepeat":repeat,
                "deltaPrimary":primary-before_primary,
                "deltaRepeat":repeat-before_repeat,
                "dentPrimary":as_int(report.get("dentPrimary"),date+":dentPrimary"),
                "dentRepeat":as_int(report.get("dentRepeat"),date+":dentRepeat"),
                "clinicPrimary":as_int(report.get("clinicPrimary"),date+":clinicPrimary"),
                "clinicRepeat":as_int(report.get("clinicRepeat"),date+":clinicRepeat"),
                "savedManualDirections":manual_by_date.get(date,{}),
            }
            control["reportedPrimary"]=control["dentPrimary"]+control["clinicPrimary"]
            control["reportedRepeat"]=control["dentRepeat"]+control["clinicRepeat"]
            control["unassignedPrimary"]=max(0,primary-control["reportedPrimary"])
            control["unassignedRepeat"]=max(0,repeat-control["reportedRepeat"])
            controls.append(control)
            if control["deltaPrimary"] or control["deltaRepeat"]:
                if control["deltaPrimary"]<0 or control["deltaRepeat"]<0:
                    raise RuntimeError("REFUSE_NEGATIVE_DELTA:"+date)
                changes.append(control)

        print("CHANGED_ROWS="+str(len(changes)))
        print("PRIMARY_DELTA_TOTAL="+str(sum(r["deltaPrimary"] for r in changes)))
        print("REPEAT_DELTA_TOTAL="+str(sum(r["deltaRepeat"] for r in changes)))
        for row in changes:
            print("CHANGE="+json.dumps(row,ensure_ascii=False,sort_keys=True))

        for date in ("2026-10-25","2026-10-26"):
            row=next((x for x in controls if x["date"]==date),None)
            if row:
                print("CONTROL_"+date.replace("-","_")+"="+json.dumps(row,ensure_ascii=False,sort_keys=True))

        print("=== FINAL ===")
        print("MANAGEMENT_OCTOBER_VISIT_REBUILD_PREFLIGHT=PASS")
        print("LIVE_WRITES=NONE")
        print("APPLY_CODE_PRESENT=NO")
    finally:
        c.close()

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"STOP={type(exc).__name__}:{exc}")
        sys.exit(1)
