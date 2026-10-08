import re
from collections import defaultdict


EXPANDED_REQUIRED = (
    "Задолженность на нач. периода",
    "Сумма со скидкой",
    "Оплачено",
    "Задолженность на конец периода",
)


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_expanded_report(text):
    lines = str(text or "").splitlines()
    if not lines:
        return False
    header = [_norm(part) for part in lines[0].split("\t")]
    return all(any(required in cell for cell in header) for required in EXPANDED_REQUIRED)


def _add_provider(total, source):
    for key in ("debt_start", "billed_net", "paid", "debt_end"):
        total[key] = float(total.get(key) or 0) + float(source.get(key) or 0)


def parse_expanded_report(
    text,
    *,
    is_staff_header,
    invoice_re,
    iso_date,
    money,
    lab_doctors,
):
    """Parse the expanded MIS «Выручка по направлениям» export.

    The file has two different layers:
    1) detailed service rows for the selected period;
    2) provider-level financial totals (opening debt, net billed, paid, closing debt).

    Detailed service rows keep their current-month service date and are suitable
    for service analytics. Provider-level «Оплачено» is intentionally kept as a
    period snapshot because it can include settlement of opening debt/deposits
    and therefore must not be reconstructed from cash receipts.
    """
    lines = str(text or "").splitlines()
    if not is_expanded_report(text):
        raise ValueError("Это не расширенный отчёт «Выручка по направлениям».")

    services_index = None
    services_totals = None
    for index, raw_line in enumerate(lines[1:], start=1):
        cols = raw_line.split("\t")
        cols += [""] * max(0, 8 - len(cols))
        if _norm(cols[0]) != "Услуги":
            continue
        services_index = index
        services_totals = {
            "debt_start": money(cols[4]) or 0.0,
            "billed_net": money(cols[5]) or 0.0,
            "paid": money(cols[6]) or 0.0,
            "debt_end": money(cols[7]) or 0.0,
        }
        break

    if services_index is None or services_totals is None:
        raise ValueError("В новом отчёте не найден раздел «Услуги».")

    balance_delta = (
        float(services_totals["debt_start"])
        + float(services_totals["billed_net"])
        - float(services_totals["paid"])
        - float(services_totals["debt_end"])
    )
    if abs(balance_delta) > 0.02:
        raise ValueError(
            "В разделе «Услуги» не сходится баланс: "
            "задолженность на начало + сумма со скидкой - оплачено "
            "не равняется задолженности на конец."
        )

    items = []
    lab_invoices = []
    providers = defaultdict(
        lambda: {
            "debt_start": 0.0,
            "billed_net": 0.0,
            "paid": 0.0,
            "debt_end": 0.0,
        }
    )
    current_staff = None
    current_patient = None
    current_date = None
    current_group = ""
    current_invoice = None

    for raw_line in lines[services_index + 1 :]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, 8 - len(cols))
        first = _norm(cols[0])
        second = _norm(cols[1])
        service = _norm(cols[2])
        qty_text = _norm(cols[3])

        if first and is_staff_header(first) and not second and not service and not qty_text:
            current_staff = first
            current_patient = None
            current_date = None
            current_group = ""
            current_invoice = None
            _add_provider(
                providers[current_staff],
                {
                    "debt_start": money(cols[4]) or 0.0,
                    "billed_net": money(cols[5]) or 0.0,
                    "paid": money(cols[6]) or 0.0,
                    "debt_end": money(cols[7]) or 0.0,
                },
            )
            continue

        if not current_staff:
            continue

        match = invoice_re.match(first)
        if match:
            current_invoice = match.group(1)
            current_date = iso_date(match.group(2))
            current_group = ""
            if current_staff in lab_doctors:
                lab_invoices.append((current_invoice, current_date))
            continue

        # Patient/header line. It can carry its own debt/payment totals, but those
        # are already included in the provider total and must not be added again.
        if first and not second and not service and not qty_text:
            current_patient = first
            continue

        amount = money(cols[5])
        paid_amount = money(cols[6])
        if not current_date or not qty_text or amount is None:
            continue

        try:
            qty = float(qty_text.replace(",", "."))
        except ValueError:
            continue

        if first:
            current_group = first

        items.append(
            {
                "staff": current_staff,
                "patient": current_patient or "",
                "date": current_date,
                "group": current_group,
                "service": service,
                "qty": qty,
                "amount": amount,
                "paid_amount": paid_amount or 0.0,
                "invoice": current_invoice,
            }
        )

    if not items:
        raise ValueError(
            "Новый отчёт «Выручка по направлениям» не содержит "
            "распознаваемых строк услуг за выбранный период."
        )

    months = {(int(row["date"][:4]), int(row["date"][5:7])) for row in items}
    if len(months) != 1:
        raise ValueError(
            "Новый отчёт «Выручка по направлениям» должен содержать "
            "один календарный месяц."
        )

    dates = sorted({row["date"] for row in items})
    provider_paid_total = round(
        sum(float(value.get("paid") or 0) for value in providers.values()),
        2,
    )

    snapshot = {
        "version": 1,
        "rule": "mis-provider-paid-period-snapshot-v1",
        "period_start": dates[0],
        "period_end": dates[-1],
        "totals": {
            key: round(float(value or 0), 2)
            for key, value in services_totals.items()
        },
        "providers": {
            name: {
                key: round(float(value.get(key) or 0), 2)
                for key in ("debt_start", "billed_net", "paid", "debt_end")
            }
            for name, value in sorted(providers.items())
        },
        "provider_paid_total": provider_paid_total,
        "provider_paid_delta": round(
            float(services_totals["paid"]) - provider_paid_total,
            2,
        ),
    }

    return items, lab_invoices, next(iter(months)), snapshot
