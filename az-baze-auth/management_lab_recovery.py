"""Explicit ZTL-only reconstruction from archived, matched IDENT originals.

No ordinary report import, cash/advertising calculation or service activation.
The archived pair is a separately selected, versioned reporting witness.
Calendar bounds are never manufactured from the archive's monthly filenames.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import management_lab as lab
import management_lab_recovery_parser as parser
import mis_archive_bundle as bundle
import mis_source_archive as archive
import paid_period_confirmation as periods

TABLE = "management_lab_recovery_runs"
SCHEMA = """CREATE TABLE IF NOT EXISTS management_lab_recovery_runs(
 id INTEGER PRIMARY KEY,
 catalog_sha256 TEXT NOT NULL UNIQUE REFERENCES mis_source_objects(sha256),
 bundle_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL,
 payload_json TEXT NOT NULL
)"""
MAX_MONTHS=120


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)


def exists(conn,table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())


def build(verified):
    manifest_raw,sources,info=verified
    catalog=json.loads(manifest_raw)
    selected=catalog.get("monthly_candidates")
    if not isinstance(selected,dict):
        raise ValueError("Historical catalog has no explicitly selected pairs")
    by_hash={row[2]:row for row in sources}
    service_keys={k.split("/",1)[1] for k in selected if k.startswith("service_prices_discounts/")}
    paid_keys={k.split("/",1)[1] for k in selected if k.startswith("paid_and_debt/")}
    if not service_keys or service_keys!=paid_keys or len(service_keys)>MAX_MONTHS:
        raise ValueError("Historical catalog must select paired services and paid sources")
    result=[]
    for month in sorted(service_keys):
        start,end=periods.month_bounds(month)
        service_sha=selected["service_prices_discounts/"+month]
        paid_sha=selected["paid_and_debt/"+month]
        service=by_hash.get(service_sha)
        paid=by_hash.get(paid_sha)
        if not service or not paid or service[0]!="services" or paid[0]!="paid_services":
            raise ValueError("Selected source kind/hash mismatch")
        if service[6] == "superseded_alternate_layout_reference_only" or paid[6] == "superseded_alternate_layout_reference_only":
            raise ValueError("Superseded source cannot be selected for recovery")
        data=parser.pair(service[1],paid[1],lab.is_lab_group)
        if not start<=data["from"]<=data["to"]<=end or (data["from"],data["to"])!=service[3:5] or service[3:5]!=paid[3:5]:
            raise ValueError("Actual observations differ from selected source bounds")
        data.update(month=month,servicesSha256=service_sha,paidSha256=paid_sha)
        result.append(data)
    return {"version":1,"catalogSha256":digest(manifest_raw),
            "bundleSha256":info["bundle_sha256"],"periods":result}


def source_baseline(conn,sources,start,end):
    # Fingerprint the exact relevant current inputs, including a removed
    # services layer. Any later change invalidates the old recovered witness.
    daily=[]
    if exists(conn,"daily_uploads"):
        daily=[(str(d),str(sha or ""),digest(str(raw).encode("utf-8")))
               for d,sha,raw in conn.execute(
                   "SELECT data_date,services_sha256,normalized_json FROM daily_uploads "
                   "WHERE data_date BETWEEN ? AND ? ORDER BY data_date",(start,end))]
    active=sorted(tuple(s.get(k) or "" for k in
                        ("kind","sourceFrom","sourceTo","sourceSha256","payloadSha256","importedAt","from","to"))
                  for s in sources if s["from"]<=end and start<=s["to"])
    return digest(canonical([daily,active]).encode("utf-8"))


def objects_present(conn,run,entry):
    # Read only catalog metadata and sizes; raw bytes were verified at the
    # explicit activation and are never returned through the report API.
    for sha,kind in ((run["catalogSha256"],"catalog"),(entry["servicesSha256"],"services"),(entry["paidSha256"],"paid_services")):
        if not isinstance(sha,str) or not archive.HEX.fullmatch(sha):
            return False
        row=conn.execute("""SELECT 1 FROM mis_source_objects o
            JOIN mis_source_events e ON e.sha256=o.sha256
            WHERE o.sha256=? AND length(o.raw)=o.size_bytes
              AND e.source_kind=? AND e.origin='verified_historical_pack'
              AND e.outcome='archive_only' AND json_extract(e.context_json,'$.bundle_sha256')=?
            LIMIT 1""",(sha,kind,run["bundleSha256"])).fetchone()
        if not row:
            return False
    return True


def activate(conn,verified,db_path):
    """Archive first, then a separate explicit, immutable ZTL selection.

    A failure in recovery never discards safely saved originals. No implicit
    activation in archive_bundle, and no update of existing report tables.
    """
    run=build(verified)
    if not exists(conn,"report_data"):
        raise ValueError("Expected existing AZ BAZE database")
    if conn.in_transaction:
        raise ValueError("Recovery requires a clean transaction")
    bundle.archive_bundle(conn,verified)
    for _kind,raw,sha,*_ in verified[1]:
        if archive.read_original(conn,sha)!=raw:
            raise ValueError("Archived original readback mismatch")
    if archive.read_original(conn,run["catalogSha256"])!=verified[0]:
        raise ValueError("Archived catalog readback mismatch")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(SCHEMA)
        # Replaying the same catalog never reactivates an older run or
        # refreshes a stale baseline. A distinct explicit selection is needed.
        old=conn.execute("SELECT id,bundle_sha256,payload_json FROM "+TABLE+" WHERE catalog_sha256=?",
                         (run["catalogSha256"],)).fetchone()
        if old:
            if old[1]!=run["bundleSha256"]:
                raise ValueError("Catalog was already selected from a different bundle")
            existing=json.loads(old[2])
            comparable={**existing,"periods":[{k:v for k,v in x.items() if k!="baseline"} for x in existing["periods"]]}
            if comparable!=run:
                raise ValueError("Stored recovery evidence differs from exact source")
            conn.rollback()
            return old[0],False
        sources=periods.read_sources(conn,periods.load_config(db_path))
        for entry in run["periods"]:
            entry["baseline"]=source_baseline(conn,sources,entry["from"],entry["to"])
            if not objects_present(conn,run,entry):
                raise ValueError("Missing archived source receipt")
        cur=conn.execute("INSERT INTO "+TABLE+"(catalog_sha256,bundle_sha256,created_at,payload_json) VALUES(?,?,?,?)",
                         (run["catalogSha256"],run["bundleSha256"],datetime.now(timezone.utc).isoformat(),canonical(run)))
        selected_id=cur.lastrowid
        conn.commit()
        return selected_id,True
    except BaseException:
        conn.rollback()
        raise


def project(conn,sources):
    """Do not parse workbooks or write DB on GET. No patient rows in output."""
    paid=lab.paid_periods(sources,conn=conn)
    days={d["date"]:d for d in lab.service_days(conn)}
    if not exists(conn,TABLE):
        return paid,list(days.values())
    row=conn.execute("SELECT catalog_sha256,bundle_sha256,payload_json FROM "+TABLE+" ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return paid,list(days.values())
    try:
        run=json.loads(row[2])
        entries=run["periods"]
        if run["version"]!=1 or run["catalogSha256"]!=row[0] or run["bundleSha256"]!=row[1] or not isinstance(entries,list) or len(entries)>MAX_MONTHS:
            return paid,list(days.values())
    except (ValueError,TypeError,KeyError):
        return paid,list(days.values())
    seen=set()
    recovered=[]
    for entry in entries:
        try:
            start,end=entry["from"],entry["to"]
            month=entry["month"]
            first,last=periods.month_bounds(month)
            if month in seen or not first<=start<=end<=last or not lab._valid_date(start) or not lab._valid_date(end):
                continue
            seen.add(month)
            if entry["baseline"]!=source_baseline(conn,sources,start,end) or not objects_present(conn,run,entry):
                continue
            known=lab._cents(entry["knownPaid"])/100
            candidate={}
            for value in entry["days"]:
                d=value["date"]
                if not lab._valid_date(d) or not start<=d<=end or d in candidate or type(value["orders"]) is not int or value["orders"]<0:
                    raise ValueError("Invalid recovered day")
                candidate[d]={"date":d,"services":lab._cents(value["services"])/100,"orders":value["orders"]}
            if not candidate or min(candidate)!=start or max(candidate)!=end:
                continue
            # Never override a different live source. Only add missing
            # observed days. A live row whose services layer was explicitly
            # removed must not be resurrected from the historical archive.
            existing_db_dates=set()
            if exists(conn,"daily_uploads"):
                existing_db_dates={d for (d,) in conn.execute("SELECT data_date FROM daily_uploads WHERE data_date BETWEEN ? AND ?",(start,end))}
            if any(days.get(d)!=candidate.get(d) for d in existing_db_dates):
                # Includes missing/cleared/invalid live service layer or a
                # differing live aggregate, but allows identical observations.
                continue
            if any(d in days and days[d]!=value for d,value in candidate.items()):
                continue
            recovered.append({"from":start,"to":end,"paid":None,"knownPaid":known,
                              "recovered":True,"reason":"archived_matched_detail"})
            for d,value in candidate.items():
                days.setdefault(d,value)
        except (ValueError,TypeError,KeyError,sqlite3.Error):
            continue
    # Separate witnesses: JS must never combine them with overlapping
    # active paid snapshots or extrapolate their observed interval.
    return [*paid,*recovered],[days[d] for d in sorted(days)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--sha256",required=True)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify",action="store_true")
    modes.add_argument("--apply",action="store_true")
    p.add_argument("--database",type=Path)
    args=p.parse_args()
    verified=bundle.read_bundle(args.bundle,args.sha256)
    run=build(verified)
    print("ZTL_MATCHED_PAIRS="+str(len(run["periods"])),flush=True)
    # Aggregate only; never log names, invoice numbers or raw workbook rows.
    for entry in run["periods"]:
        print(canonical({"month":entry["month"],"from":entry["from"],"to":entry["to"],
                         "rows":entry["rowCount"],"services":sum(lab._cents(d["services"]) for d in entry["days"])/100,
                         "orders":sum(d["orders"] for d in entry["days"]),"knownPaid":entry["knownPaid"],
                         "fullPaid":None}),flush=True)
    if args.verify:
        print("ZTL_RECOVERY_VERIFY=PASS; READ_ONLY=YES",flush=True)
        return
    if args.database is None or args.database.is_symlink() or not args.database.is_file():
        raise ValueError("Existing SQLite database is required")
    conn=sqlite3.connect(args.database.resolve().as_uri()+"?mode=rw",uri=True,timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA quick_check").fetchone()[0]!="ok":
            raise ValueError("Database integrity check failed")
        selected,created=activate(conn,verified,args.database)
        print("MIS_ARCHIVE=PASS; ZTL_RECOVERY_SELECTION="+str(selected)+"; CREATED="+str(created)+"; BUSINESS_REIMPORT=NONE",flush=True)
        sources=periods.read_sources(conn,periods.load_config(args.database))
        projected,_=project(conn,sources)
        print("ZTL_RECOVERED_WITNESSES="+str(sum(bool(x.get("recovered")) for x in projected)),flush=True)
    finally:
        conn.close()


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        # Avoid exposing workbook/patient text through parser exception logs.
        print("ZTL_RECOVERY_STOP="+type(exc).__name__,file=sys.stderr,flush=True)
        sys.exit(1)
