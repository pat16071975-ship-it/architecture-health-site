import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta

TRACKING_START = "2026-10-27"
MIS_PROVIDER = "mis"
UNKNOWN_SOURCE_KEY = "unknown"
UNKNOWN_SOURCE_NAME = "Неизвестно"
UNKNOWN_CHANNEL_NAME = "Неизвестно"

SCHEMA = """
CREATE TABLE IF NOT EXISTS advertising_mis_paid_ingests (
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    paid_total REAL NOT NULL,
    detailed_paid_total REAL NOT NULL,
    line_count INTEGER NOT NULL,
    PRIMARY KEY(period_start, period_end)
);
CREATE TABLE IF NOT EXISTS advertising_mis_paid_lines (
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    line_key TEXT NOT NULL,
    patient_token TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    staff TEXT NOT NULL,
    invoice TEXT NOT NULL,
    service_date TEXT NOT NULL,
    group_name TEXT NOT NULL,
    service_name TEXT NOT NULL,
    qty REAL NOT NULL,
    billed_amount REAL NOT NULL,
    paid_amount REAL NOT NULL,
    closing_debt REAL NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(period_start, period_end, line_key)
);
CREATE INDEX IF NOT EXISTS idx_ad_mis_paid_lines_service_date
    ON advertising_mis_paid_lines(service_date);
CREATE INDEX IF NOT EXISTS idx_ad_mis_paid_lines_patient
    ON advertising_mis_paid_lines(patient_token, service_date);
CREATE TABLE IF NOT EXISTS advertising_clinic_daily (
    provider TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    target_leads INTEGER,
    leads_with_primary INTEGER,
    primary_visits INTEGER,
    primary_paid REAL,
    primary_services REAL,
    repeat_visits INTEGER,
    repeat_paid REAL,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, metric_date, source_key, campaign_id, campaign_name)
);
CREATE TABLE IF NOT EXISTS advertising_cohort_daily (
    provider TEXT NOT NULL,
    cohort_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    new_patients INTEGER,
    new_patient_30d_paid REAL,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, cohort_date, source_key, campaign_id, campaign_name)
);
"""


def init_schema(conn):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _num(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _person_key(value):
    text = str(value or "").lower().replace("ё", "е")
    parts = re.findall(r"[a-zа-я]+", text)
    if not parts:
        return ""
    surname = parts[0]
    initials = "".join(part[0] for part in parts[1:3])
    return surname + "_" + initials


def patient_token(value):
    key = _person_key(value)
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _line_identity(item):
    payload = [
        patient_token(item.get("patient")),
        str(item.get("staff") or "").strip(),
        str(item.get("invoice") or "").strip(),
        str(item.get("date") or "").strip(),
        str(item.get("group") or "").strip(),
        str(item.get("service") or "").strip(),
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _aggregate_report_lines(report):
    rows = {}
    for item in report.get("items") or []:
        line_key = _line_identity(item)
        row = rows.setdefault(
            line_key,
            {
                "line_key": line_key,
                "patient_token": patient_token(item.get("patient")),
                "patient_name": str(item.get("patient") or "").strip(),
                "staff": str(item.get("staff") or "").strip(),
                "invoice": str(item.get("invoice") or "").strip(),
                "service_date": str(item.get("date") or "").strip(),
                "group_name": str(item.get("group") or "").strip(),
                "service_name": str(item.get("service") or "").strip(),
                "qty": 0.0,
                "billed_amount": 0.0,
                "paid_amount": 0.0,
                "closing_debt": 0.0,
            },
        )
        row["qty"] = round(row["qty"] + _num(item.get("qty")), 4)
        row["billed_amount"] = round(row["billed_amount"] + _num(item.get("amount")), 2)
        row["paid_amount"] = round(row["paid_amount"] + _num(item.get("paid_amount")), 2)
        row["closing_debt"] = round(row["closing_debt"] + _num(item.get("closing_debt")), 2)
    return rows


def capture_paid_report(conn, report, source_filename, source_sha256, imported_at):
    init_schema(conn)
    period_start = str(report.get("period_start") or "")
    period_end = str(report.get("period_end") or "")
    if not period_start or not period_end:
        raise ValueError("Advertising MIS bridge: paid report period is missing.")
    if period_end < TRACKING_START:
        return {"captured": False, "reason": "before_tracking_start", "lines": 0}

    rows = _aggregate_report_lines(report)
    detailed_paid = round(sum(row["paid_amount"] for row in rows.values()), 2)
    paid_total = round(_num((report.get("totals") or {}).get("paid")), 2)

    conn.execute(
        "DELETE FROM advertising_mis_paid_lines WHERE period_start=? AND period_end=?",
        (period_start, period_end),
    )
    conn.execute(
        """
        INSERT INTO advertising_mis_paid_ingests(
            period_start,period_end,source_filename,source_sha256,imported_at,
            paid_total,detailed_paid_total,line_count
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(period_start,period_end) DO UPDATE SET
            source_filename=excluded.source_filename,
            source_sha256=excluded.source_sha256,
            imported_at=excluded.imported_at,
            paid_total=excluded.paid_total,
            detailed_paid_total=excluded.detailed_paid_total,
            line_count=excluded.line_count
        """,
        (
            period_start,
            period_end,
            source_filename or "",
            source_sha256 or "",
            imported_at,
            paid_total,
            detailed_paid,
            len(rows),
        ),
    )
    for row in rows.values():
        conn.execute(
            """
            INSERT INTO advertising_mis_paid_lines(
                period_start,period_end,line_key,patient_token,patient_name,staff,invoice,
                service_date,group_name,service_name,qty,billed_amount,paid_amount,
                closing_debt,source_sha256,imported_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                period_start,
                period_end,
                row["line_key"],
                row["patient_token"],
                row["patient_name"],
                row["staff"],
                row["invoice"],
                row["service_date"],
                row["group_name"],
                row["service_name"],
                row["qty"],
                row["billed_amount"],
                row["paid_amount"],
                row["closing_debt"],
                source_sha256 or "",
                imported_at,
            ),
        )
    return {
        "captured": True,
        "period_start": period_start,
        "period_end": period_end,
        "lines": len(rows),
        "paid_total": paid_total,
        "detailed_paid_total": detailed_paid,
        "unmapped_paid": round(paid_total - detailed_paid, 2),
    }


def _fetch_ingests(conn):
    return conn.execute(
        """
        SELECT period_start,period_end,source_filename,source_sha256,imported_at,
               paid_total,detailed_paid_total,line_count
        FROM advertising_mis_paid_ingests
        WHERE period_end>=?
        ORDER BY period_start,period_end
        """,
        (TRACKING_START,),
    ).fetchall()


def _snapshot_lines(conn, period_start, period_end):
    rows = conn.execute(
        """
        SELECT line_key,patient_token,patient_name,staff,invoice,service_date,
               group_name,service_name,qty,billed_amount,paid_amount,closing_debt
        FROM advertising_mis_paid_lines
        WHERE period_start=? AND period_end=?
        """,
        (period_start, period_end),
    ).fetchall()
    return {str(row["line_key"]): dict(row) for row in rows}


def _payment_candidates(conn):
    groups = defaultdict(list)
    for row in _fetch_ingests(conn):
        groups[str(row["period_start"])].append(str(row["period_end"]))

    # Evidence is merged per payment date and per source line. A same-day report
    # is stronger evidence for a line than a cumulative delta, but it must not
    # hide cumulative deltas for other lines (for example a later payment on a
    # service delivered on an earlier day).
    evidence = defaultdict(dict)

    for period_start, ends in groups.items():
        ends = sorted(set(ends))
        previous = {}
        previous_end = None
        for period_end in ends:
            current = _snapshot_lines(conn, period_start, period_end)
            event_date = period_end
            selected = None
            rank = None

            if period_start == period_end:
                selected = {
                    key: max(0.0, round(_num(row.get("paid_amount")), 2))
                    for key, row in current.items()
                }
                rank = 2
            elif previous_end:
                expected = (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat()
                if expected == period_end:
                    selected = {
                        key: max(
                            0.0,
                            round(
                                _num(row.get("paid_amount"))
                                - _num((previous.get(key) or {}).get("paid_amount")),
                                2,
                            ),
                        )
                        for key, row in current.items()
                    }
                    rank = 1

            if selected is not None and event_date >= TRACKING_START:
                for key, amount in selected.items():
                    row = current.get(key)
                    if row is None:
                        continue
                    existing = evidence[event_date].get(key)
                    candidate_rank = (rank, -date.fromisoformat(period_start).toordinal())
                    if existing is not None and candidate_rank <= existing["rank"]:
                        continue
                    evidence[event_date][key] = {
                        "rank": candidate_rank,
                        "event": {
                            "line_key": key,
                            "patient_token": str(row.get("patient_token") or ""),
                            "staff": str(row.get("staff") or ""),
                            "invoice": str(row.get("invoice") or ""),
                            "service_date": str(row.get("service_date") or ""),
                            "paid_delta": amount,
                        },
                    }

            previous = current
            previous_end = period_end

    result = {}
    for event_date, by_line in evidence.items():
        result[event_date] = {
            "events": [item["event"] for item in by_line.values()],
        }
    return result


def _latest_line_facts(conn):
    rows = conn.execute(
        """
        SELECT period_start,period_end,line_key,patient_token,patient_name,staff,invoice,
               service_date,group_name,service_name,qty,billed_amount,paid_amount,
               closing_debt,imported_at
        FROM advertising_mis_paid_lines
        WHERE period_end>=?
        ORDER BY period_end,period_start,imported_at
        """,
        (TRACKING_START,),
    ).fetchall()
    latest = {}
    for row in rows:
        latest[str(row["line_key"])] = dict(row)
    return latest


def _load_completed(conn):
    try:
        rows = conn.execute(
            "SELECT data_date,normalized_json FROM daily_uploads ORDER BY data_date"
        ).fetchall()
    except Exception:
        return {
            "coverage": set(),
            "visits": [],
            "first_visit": {},
            "counts": {},
            "classification": {},
            "seen_before_tracking": set(),
        }

    coverage = set()
    visits = []
    first_visit = {}
    counts = defaultdict(lambda: {"primary": 0, "repeat": 0})
    grouped = defaultdict(list)

    for row in rows:
        data_date = str(row["data_date"])
        if data_date >= TRACKING_START:
            coverage.add(data_date)
        try:
            normalized = json.loads(row["normalized_json"])
        except (TypeError, ValueError):
            continue
        for visit in (normalized or {}).get("visits") or []:
            visit_date = str(visit.get("date") or data_date)
            token = patient_token(visit.get("patient"))
            kind = str(visit.get("kind") or "")
            if not token or not visit_date or not kind:
                continue
            visits.append(
                {
                    "date": visit_date,
                    "patient_token": token,
                    "kind": kind,
                }
            )
            old = first_visit.get(token)
            if old is None or visit_date < old:
                first_visit[token] = visit_date
            grouped[(visit_date, token)].append(kind)
            if visit_date >= TRACKING_START:
                if kind == "Первичные":
                    counts[visit_date]["primary"] += 1
                elif kind in {"Повторные", "Отконсультированные"}:
                    counts[visit_date]["repeat"] += 1

    latest_lines = _latest_line_facts(conn)
    seen_before_tracking = {
        str(row.get("patient_token") or "")
        for row in latest_lines.values()
        if str(row.get("patient_token") or "")
        and str(row.get("service_date") or "")
        and str(row.get("service_date") or "") < TRACKING_START
    }
    staff_by_visit = defaultdict(set)
    for row in latest_lines.values():
        service_date = str(row.get("service_date") or "")
        token = str(row.get("patient_token") or "")
        staff = str(row.get("staff") or "")
        if service_date and token and staff:
            staff_by_visit[(service_date, token)].add(staff)

    classification = {}
    for key, kinds in grouped.items():
        candidates = sorted(staff_by_visit.get(key) or [])
        normalized_kinds = [
            "primary" if kind == "Первичные"
            else "repeat" if kind in {"Повторные", "Отконсультированные"}
            else ""
            for kind in kinds
        ]
        normalized_kinds = [kind for kind in normalized_kinds if kind]
        if not candidates or not normalized_kinds:
            continue
        unique_kinds = set(normalized_kinds)
        if len(candidates) == 1 and len(unique_kinds) == 1:
            classification[(key[0], key[1], candidates[0])] = normalized_kinds[0]
        elif len(unique_kinds) == 1 and len(normalized_kinds) >= len(candidates):
            for staff in candidates:
                classification[(key[0], key[1], staff)] = normalized_kinds[0]

    return {
        "coverage": coverage,
        "visits": visits,
        "first_visit": first_visit,
        "counts": dict(counts),
        "classification": classification,
        "seen_before_tracking": seen_before_tracking,
    }


def _import_id(conn):
    state = []
    for row in _fetch_ingests(conn):
        state.append(
            [
                str(row["period_start"]),
                str(row["period_end"]),
                str(row["source_sha256"]),
            ]
        )
    try:
        row = conn.execute(
            "SELECT MAX(data_date),MAX(revision) FROM daily_uploads"
        ).fetchone()
        state.append([str(row[0] or ""), str(row[1] or "")] if row else ["", ""])
    except Exception:
        state.append(["", ""])
    return "mis-auto-" + hashlib.sha256(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def rebuild(conn, updated_at):
    init_schema(conn)
    completed = _load_completed(conn)
    classification = completed["classification"]
    payment_candidates = _payment_candidates(conn)
    evidence_dates = set(payment_candidates)
    latest_lines = _latest_line_facts(conn)

    primary_paid = defaultdict(float)
    repeat_paid = defaultdict(float)
    patient_paid = defaultdict(lambda: defaultdict(float))
    unclassified_paid = defaultdict(float)

    for payment_date, candidate in payment_candidates.items():
        for event in candidate["events"]:
            amount = round(_num(event.get("paid_delta")), 2)
            if amount <= 0:
                continue
            token = str(event.get("patient_token") or "")
            if token:
                patient_paid[token][payment_date] += amount
            kind = classification.get(
                (
                    str(event.get("service_date") or ""),
                    token,
                    str(event.get("staff") or ""),
                )
            )
            if kind == "primary":
                primary_paid[payment_date] += amount
            elif kind == "repeat":
                repeat_paid[payment_date] += amount
            else:
                unclassified_paid[payment_date] += amount

    primary_services = defaultdict(float)
    service_evidence_dates = set()
    for row in latest_lines.values():
        service_date = str(row.get("service_date") or "")
        if not service_date or service_date < TRACKING_START:
            continue
        service_evidence_dates.add(service_date)
        kind = classification.get(
            (
                service_date,
                str(row.get("patient_token") or ""),
                str(row.get("staff") or ""),
            )
        )
        if kind == "primary":
            primary_services[service_date] += _num(row.get("billed_amount"))

    import_id = _import_id(conn)
    clinic_dates = sorted(
        {day for day in completed["coverage"] if day >= TRACKING_START}
        | {day for day in evidence_dates if day >= TRACKING_START}
        | service_evidence_dates
    )

    conn.execute(
        "DELETE FROM advertising_clinic_daily WHERE provider=? AND metric_date>=?",
        (MIS_PROVIDER, TRACKING_START),
    )
    for day in clinic_dates:
        visit_counts = completed["counts"].get(day, {})
        has_clinical = day in completed["coverage"]
        has_payments = day in evidence_dates
        has_services = day in service_evidence_dates
        conn.execute(
            """
            INSERT INTO advertising_clinic_daily(
                provider,metric_date,source_key,source_name,channel_name,
                campaign_id,campaign_name,target_leads,leads_with_primary,
                primary_visits,primary_paid,primary_services,repeat_visits,
                repeat_paid,updated_at,import_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                MIS_PROVIDER,
                day,
                UNKNOWN_SOURCE_KEY,
                UNKNOWN_SOURCE_NAME,
                UNKNOWN_CHANNEL_NAME,
                "",
                "",
                None,
                None,
                int(visit_counts.get("primary") or 0) if has_clinical else None,
                round(primary_paid.get(day, 0.0), 2) if has_payments else None,
                round(primary_services.get(day, 0.0), 2) if has_services else None,
                int(visit_counts.get("repeat") or 0) if has_clinical else None,
                round(repeat_paid.get(day, 0.0), 2) if has_payments else None,
                updated_at,
                import_id,
            ),
        )

    new_by_date = defaultdict(set)
    seen_before_tracking = completed.get("seen_before_tracking") or set()
    for token, first_date in completed["first_visit"].items():
        if first_date >= TRACKING_START and token not in seen_before_tracking:
            new_by_date[first_date].add(token)

    conn.execute(
        "DELETE FROM advertising_cohort_daily WHERE provider=? AND cohort_date>=?",
        (MIS_PROVIDER, TRACKING_START),
    )
    cohort_dates = sorted(
        day for day in completed["coverage"] if day >= TRACKING_START
    )
    latest_payment_date = max(evidence_dates) if evidence_dates else None
    for cohort_date in cohort_dates:
        tokens = new_by_date.get(cohort_date, set())
        paid_30d = None
        if latest_payment_date and latest_payment_date >= cohort_date:
            window_end = min(
                date.fromisoformat(cohort_date) + timedelta(days=29),
                date.fromisoformat(latest_payment_date),
            ).isoformat()
            total = 0.0
            for token in tokens:
                for payment_date, amount in patient_paid.get(token, {}).items():
                    if cohort_date <= payment_date <= window_end:
                        total += amount
            paid_30d = round(total, 2)
        conn.execute(
            """
            INSERT INTO advertising_cohort_daily(
                provider,cohort_date,source_key,source_name,channel_name,
                campaign_id,campaign_name,new_patients,new_patient_30d_paid,
                updated_at,import_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                MIS_PROVIDER,
                cohort_date,
                UNKNOWN_SOURCE_KEY,
                UNKNOWN_SOURCE_NAME,
                UNKNOWN_CHANNEL_NAME,
                "",
                "",
                len(tokens),
                paid_30d,
                updated_at,
                import_id,
            ),
        )

    return {
        "tracking_start": TRACKING_START,
        "clinic_rows": len(clinic_dates),
        "cohort_rows": len(cohort_dates),
        "payment_evidence_dates": len(evidence_dates),
        "latest_payment_date": latest_payment_date,
        "unclassified_paid": round(sum(unclassified_paid.values()), 2),
        "import_id": import_id,
    }
