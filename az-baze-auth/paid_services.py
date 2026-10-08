import json
import re
from datetime import date, datetime
from io import BytesIO

from openpyxl import load_workbook


STAFF_HEADER_RE = re.compile(
    r"^(?:[A-Za-zА-ЯЁа-яё-]+(?:\s+[A-Za-zА-ЯЁа-яё-]+)*)\s+[A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.)?$"
)
INVOICE_RE = re.compile(r"^Счет №(\d+) от (\d{2}\.\d{2}\.\d{4})(?:\s+\d{1,2}:\d{2}:\d{2})?$")
DATE_TIME_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y")

SCHEMA = """
CREATE TABLE IF NOT EXISTS service_payment_snapshots (
    as_of_date TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_by INTEGER,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_service_payment_snapshots_month
    ON service_payment_snapshots(month, as_of_date);

CREATE TABLE IF NOT EXISTS service_payment_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date TEXT NOT NULL,
    month TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_by INTEGER,
    imported_at TEXT NOT NULL,
    archived_by INTEGER,
    archived_at TEXT NOT NULL,
    decision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_service_payment_versions_date
    ON service_payment_versions(as_of_date, id);
"""


class NotPaidServicesReport(ValueError):
    pass


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _num(value):
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").replace("₽", "").strip()
    if text in {"", "-", "—"}:
        return 0.0
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Не удалось прочитать денежное значение «{value}».") from exc


def _qty(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _iso(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = _text(value)
    for fmt in DATE_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _money_row(values):
    return {
        "opening": round(_num(values[0]), 2),
        "billed": round(_num(values[1]), 2),
        "paid": round(_num(values[2]), 2),
        "closing": round(_num(values[3]), 2),
    }


def _assert_balance(label, values, tolerance=0.02):
    delta = round(values["opening"] + values["billed"] - values["paid"] - values["closing"], 2)
    if abs(delta) > tolerance:
        raise ValueError(
            f"Нарушен баланс нового отчёта для «{label}»: "
            "задолженность на начало + сумма со скидкой - оплачено "
            "не равны задолженности на конец."
        )
    return delta


def _find_sheet(workbook):
    required = {
        "Группа услуг",
        "Услуги",
        "Задолженность на нач. периода",
        "Сумма со скидкой",
        "Оплачено",
        "Задолженность на конец периода",
    }
    for ws in workbook.worksheets:
        headers = {_text(cell.value) for cell in ws[1] if _text(cell.value)}
        if required.issubset(headers):
            return ws
    raise NotPaidServicesReport(
        "Не найден лист нового отчёта «Выручка по направлениям» "
        "с колонками задолженности и оплаты."
    )


def _provider_header(row, known_staff=None):
    first = _text(row[0])
    return bool(
        first
        and (first in set(known_staff or ()) or STAFF_HEADER_RE.fullmatch(first))
        and not _text(row[1])
        and not _text(row[2])
        and not _text(row[3])
    )


def parse_bytes(raw, filename="", known_staff=None):
    if not raw:
        raise ValueError("Выбран пустой файл «Выручка по направлениям».")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла «Выручка по направлениям» превышает 25 МБ.")

    try:
        wb = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Не удалось открыть новый отчёт «Выручка по направлениям».") from exc

    try:
        ws = _find_sheet(wb)
        section_found = False
        section_totals = None
        providers = {}
        items = []
        invoices = []
        current_staff = None
        current_patient = ""
        current_invoice = None
        current_date = None
        current_group = ""

        for row in ws.iter_rows(values_only=True):
            values = list(row[:8]) + [None] * max(0, 8 - len(row[:8]))
            first = _text(values[0])
            second = _text(values[1])
            service = _text(values[2])
            qty = _qty(values[3])

            if not section_found:
                if first == "Услуги":
                    section_found = True
                    section_totals = _money_row(values[4:8])
                    _assert_balance("Услуги", section_totals)
                continue

            if first == "Авансы":
                break

            if first == "Неоплаченные услуги на начало периода":
                # These rows explain opening debt. Their payment is already
                # included in the provider-level «Оплачено» total and must not
                # become a second service item.
                continue

            if _provider_header(values, known_staff=known_staff):
                current_staff = first
                current_patient = ""
                current_invoice = None
                current_date = None
                current_group = ""
                summary = _money_row(values[4:8])
                _assert_balance(current_staff, summary)
                providers[current_staff] = summary
                continue

            if not current_staff:
                continue

            match = INVOICE_RE.match(first)
            if match:
                current_invoice = match.group(1)
                current_date = datetime.strptime(match.group(2), "%d.%m.%Y").strftime("%Y-%m-%d")
                current_group = ""
                invoices.append(
                    {
                        "staff": current_staff,
                        "invoice": current_invoice,
                        "date": current_date,
                    }
                )
                continue

            # Patient header between staff and invoice rows.
            if first and not second and not service and qty is None:
                current_patient = first
                continue

            if not service or qty is None:
                continue

            service_date = _iso(values[1]) or current_date
            if not service_date:
                continue

            if first:
                current_group = first

            billed = _num(values[5])
            paid = _num(values[6])
            closing = _num(values[7])
            items.append(
                {
                    "staff": current_staff,
                    "patient": current_patient,
                    "date": service_date,
                    "group": current_group,
                    "service": service,
                    "qty": qty,
                    "amount": round(billed, 2),
                    "paid_amount": round(paid, 2),
                    "closing_debt": round(closing, 2),
                    "invoice": current_invoice,
                }
            )

        if not section_found or section_totals is None:
            raise ValueError("В новом отчёте не найден раздел «Услуги».")
        if not providers:
            raise ValueError("В новом отчёте не найдены строки врачей/исполнителей.")
        if not items:
            raise ValueError("В новом отчёте не найдены строки услуг выбранного периода.")

        months = sorted({str(item["date"])[:7] for item in items if item.get("date")})
        if len(months) != 1:
            raise ValueError("Новый отчёт «Выручка по направлениям» должен содержать один календарный месяц.")
        month = months[0]
        period_end = max(str(item["date"]) for item in items if item.get("date"))

        provider_sum = {
            key: round(sum(value[key] for value in providers.values()), 2)
            for key in ("opening", "billed", "paid", "closing")
        }
        residual = {
            key: round(section_totals[key] - provider_sum[key], 2)
            for key in ("opening", "billed", "paid", "closing")
        }
        _assert_balance("нераспознанная часть отчёта", residual)

        return {
            "source_type": "paid_services_v1",
            "sheet": ws.title,
            "filename": filename or "",
            "month": month,
            "period_end": period_end,
            "totals": section_totals,
            "providers": providers,
            "provider_residual": residual,
            "items": items,
            "invoices": invoices,
        }
    finally:
        wb.close()


def direction_summary(report, dentists, structure_doctors, lab_doctors, ignored=None):
    ignored = set(ignored or ())
    providers = report.get("providers") or {}
    dentists = dict(dentists or {})
    structure_doctors = dict(structure_doctors or {})
    lab_doctors = dict(lab_doctors or {})

    dent_rows = {}
    structure_rows = {}
    lab_rows = {}
    unknown = {}
    ignored_paid = 0.0

    for source_name, values in providers.items():
        paid = round(_num((values or {}).get("paid")), 2)
        if source_name in dentists:
            display = dentists[source_name]
            dent_rows[display] = round(dent_rows.get(display, 0.0) + paid, 2)
        elif source_name in structure_doctors:
            display = structure_doctors[source_name]
            structure_rows[display] = round(structure_rows.get(display, 0.0) + paid, 2)
        elif source_name in lab_doctors:
            display = lab_doctors[source_name]
            lab_rows[display] = round(lab_rows.get(display, 0.0) + paid, 2)
        elif source_name in ignored:
            ignored_paid = round(ignored_paid + paid, 2)
        elif abs(paid) > 0.004 or abs(_num((values or {}).get("billed"))) > 0.004:
            unknown[source_name] = {
                "paid": paid,
                "billed": round(_num((values or {}).get("billed")), 2),
                "opening": round(_num((values or {}).get("opening")), 2),
                "closing": round(_num((values or {}).get("closing")), 2),
            }

    dent_total = round(sum(dent_rows.values()), 2)
    structure_total = round(sum(structure_rows.values()), 2)
    lab_total = round(sum(lab_rows.values()), 2)
    residual_paid = round(_num((report.get("provider_residual") or {}).get("paid")), 2)
    paid_total = round(_num((report.get("totals") or {}).get("paid")), 2)
    classified_total = round(dent_total + structure_total + lab_total, 2)

    return {
        "dentists": dent_rows,
        "clinicDocs": structure_rows,
        "labDocs": lab_rows,
        "dentPaid": dent_total,
        "structurePaid": structure_total,
        "labPaid": lab_total,
        "classifiedPaid": classified_total,
        "paidTotal": paid_total,
        "unknownProviders": unknown,
        "unknownPaid": round(sum(item["paid"] for item in unknown.values()), 2),
        "ignoredPaid": ignored_paid,
        "nonProviderPaid": residual_paid,
        "unclassifiedPaid": round(paid_total - classified_total, 2),
    }


def compact_report(report):
    return {
        "version": 1,
        "month": str(report.get("month") or ""),
        "as_of_date": str(report.get("period_end") or ""),
        "totals": dict(report.get("totals") or {}),
        "providers": {
            str(name): dict(values or {})
            for name, values in (report.get("providers") or {}).items()
        },
        "provider_residual": dict(report.get("provider_residual") or {}),
    }


def init_schema(conn):
    conn.executescript(SCHEMA)


def store_snapshot(
    conn,
    report,
    source_filename,
    source_sha256,
    actor_id,
    imported_at,
    decision="append",
):
    init_schema(conn)
    payload = compact_report(report)
    as_of_date = payload["as_of_date"]
    month = payload["month"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of_date):
        raise ValueError("Нельзя сохранить новый отчёт: не определена дата среза.")
    if month != as_of_date[:7]:
        raise ValueError("Нельзя сохранить новый отчёт: месяц и дата среза не совпадают.")

    existing = conn.execute(
        "SELECT * FROM service_payment_snapshots WHERE as_of_date=?",
        (as_of_date,),
    ).fetchone()
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if existing:
        old_payload = existing["payload_json"] if hasattr(existing, "keys") else existing[2]
        old_sha = existing["source_sha256"] if hasattr(existing, "keys") else existing[4]
        if str(old_sha) == str(source_sha256) and str(old_payload) == payload_json:
            return "identical"
        conn.execute(
            """
            INSERT INTO service_payment_versions(
                as_of_date,month,payload_json,source_filename,source_sha256,
                imported_by,imported_at,archived_by,archived_at,decision
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                existing["as_of_date"],
                existing["month"],
                existing["payload_json"],
                existing["source_filename"],
                existing["source_sha256"],
                existing["imported_by"],
                existing["imported_at"],
                actor_id,
                imported_at,
                decision,
            ),
        )
        conn.execute(
            """
            UPDATE service_payment_snapshots
            SET month=?,payload_json=?,source_filename=?,source_sha256=?,
                imported_by=?,imported_at=?
            WHERE as_of_date=?
            """,
            (
                month,
                payload_json,
                source_filename or "Выручка по направлениям",
                source_sha256,
                actor_id,
                imported_at,
                as_of_date,
            ),
        )
        return "replaced"

    conn.execute(
        """
        INSERT INTO service_payment_snapshots(
            as_of_date,month,payload_json,source_filename,source_sha256,
            imported_by,imported_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            as_of_date,
            month,
            payload_json,
            source_filename or "Выручка по направлениям",
            source_sha256,
            actor_id,
            imported_at,
        ),
    )
    return "imported"


def _snapshot_payload(row):
    if not row:
        return None
    raw = row["payload_json"] if hasattr(row, "keys") else row[0]
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("version") == 1 else None


def snapshot_for_date(conn, data_date):
    init_schema(conn)
    month = str(data_date or "")[:7]
    row = conn.execute(
        """
        SELECT payload_json
        FROM service_payment_snapshots
        WHERE month=? AND as_of_date<=?
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        (month, str(data_date)),
    ).fetchone()
    return _snapshot_payload(row)


def apply_snapshot(record, snapshot, dentists, structure_doctors, lab_doctors, ignored=None):
    if not isinstance(record, dict) or not isinstance(snapshot, dict):
        return record
    report = {
        "totals": snapshot.get("totals") or {},
        "providers": snapshot.get("providers") or {},
        "provider_residual": snapshot.get("provider_residual") or {},
    }
    summary = direction_summary(
        report,
        dentists,
        structure_doctors,
        lab_doctors,
        ignored=ignored,
    )
    totals = report["totals"]
    record["paidDataComplete"] = True
    record["paidAsOf"] = str(snapshot.get("as_of_date") or "")
    record["paidDentists"] = summary["dentists"]
    record["paidClinicDocs"] = summary["clinicDocs"]
    record["paidLabDocs"] = summary["labDocs"]
    record["paidDentistry"] = summary["dentPaid"]
    record["paidStructure"] = summary["structurePaid"]
    record["paidLab"] = summary["labPaid"]
    record["paidServicesTotal"] = summary["paidTotal"]
    record["paidClassifiedTotal"] = summary["classifiedPaid"]
    record["paidUnclassified"] = summary["unclassifiedPaid"]
    record["serviceDebtOpening"] = round(_num(totals.get("opening")), 2)
    record["serviceBilled"] = round(_num(totals.get("billed")), 2)
    record["serviceDebtClosing"] = round(_num(totals.get("closing")), 2)
    return record


def overlay_record_map(conn, month, records, dentists, structure_doctors, lab_doctors, ignored=None):
    if not records:
        return records
    for data_date, record in records.items():
        if str(data_date)[:7] != str(month):
            continue
        snapshot = snapshot_for_date(conn, data_date)
        if snapshot:
            apply_snapshot(
                record,
                snapshot,
                dentists,
                structure_doctors,
                lab_doctors,
                ignored=ignored,
            )
    return records


def latest_loaded_date(conn):
    init_schema(conn)
    row = conn.execute(
        "SELECT MAX(as_of_date) FROM service_payment_snapshots"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def snapshot_months(conn):
    init_schema(conn)
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT month FROM service_payment_snapshots ORDER BY month"
        ).fetchall()
    ]


def overlay_stored_month(
    conn,
    month,
    actor_id,
    updated_at,
    dentists,
    structure_doctors,
    lab_doctors,
    ignored=None,
):
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT date,payload
        FROM report_data
        WHERE substr(date,1,7)=?
        ORDER BY date
        """,
        (str(month),),
    ).fetchall()
    changed = 0
    for row in rows:
        data_date = str(row["date"] if hasattr(row, "keys") else row[0])
        raw = row["payload"] if hasattr(row, "keys") else row[1]
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        snapshot = snapshot_for_date(conn, data_date)
        if not snapshot:
            continue
        apply_snapshot(
            record,
            snapshot,
            dentists,
            structure_doctors,
            lab_doctors,
            ignored=ignored,
        )
        conn.execute(
            """
            UPDATE report_data
            SET payload=?,updated_by=?,updated_at=?
            WHERE date=?
            """,
            (
                json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                actor_id,
                updated_at,
                data_date,
            ),
        )
        changed += 1
    return changed
