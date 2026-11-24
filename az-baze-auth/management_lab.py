"""Group-based ZTL evidence for the management report only.

The paid workbook and the original service export are different sources.
Never read/alter cash allocation, provider totals or advertising data here.
"""
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

EVIDENCE_KEY = "management_lab_v1"


def is_lab_group(value):
    if not isinstance(value, str):
        return False
    # IDENT group paths may have a parent and a qualified laboratory name.
    # Match the group component, NOT a service description or provider name.
    return any(re.fullmatch(r"зуботехническая лаборатория(?:\s*\([^()]+\))?",
                            " ".join(part.casefold().split()))
               for part in re.split(r"[/\\]", value))


def _cents(value):
    if value is None or isinstance(value, bool) or value == "":
        raise ValueError("missing amount")
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError("invalid monetary precision")
        result = int(amount)
    except (InvalidOperation, OverflowError, TypeError, ValueError) as exc:
        raise ValueError("invalid amount") from exc
    if abs(result) > 9007199254740991:
        raise ValueError("amount out of range")
    return result


def _valid_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def paid_evidence(report):
    """Persist only aggregate, reconciled group evidence from a new raw workbook.

    Provider totals can contain payment of earlier debt. A residual with no
    group evidence is not assigned by guesswork, even to a laboratory employee.
    """
    result = {"version": 1, "paid": None, "reason": "missing_detail"}
    items = report.get("items")
    providers = report.get("providers")
    if not isinstance(items, list) or not items or not isinstance(providers, dict) or not providers:
        return result
    by_provider = defaultdict(int)
    lab = 0
    try:
        for item in items:
            if not isinstance(item, dict) or item.get("staff") not in providers:
                raise ValueError("unknown provider in detail")
            amount = _cents(item.get("paid_amount"))
            group = item.get("group")
            if amount and (not isinstance(group, str) or not group.strip()):
                raise ValueError("missing paid group")
            by_provider[item["staff"]] += amount
            if is_lab_group(group):
                lab += amount
        totals = {name: _cents(value.get("paid")) for name, value in providers.items()}
        total = _cents(report.get("totals", {}).get("paid"))
        residual = _cents(report.get("provider_residual", {}).get("paid"))
        if (residual or sum(totals.values()) != total
                or any(by_provider[name] != amount for name, amount in totals.items())):
            return {"version": 1, "paid": None, "reason": "unallocated_payment",
                    "knownPaid": lab / 100}
        if abs(lab) > 9007199254740991:
            raise ValueError("amount out of range")
    except (TypeError, AttributeError, ValueError, OverflowError):
        return {"version": 1, "paid": None, "reason": "invalid_detail"}
    return {"version": 1, "paid": lab / 100, "reason": "reconciled"}


def paid_periods(sources):
    """Same validated source intervals as the doctor report; no reimport."""
    by_range = {}
    # read_sources orders snapshots first and explicit periods second.
    # An explicit source of the same range supersedes a snapshot, even if
    # its new group evidence is unavailable.
    for source in sources:
        start, end = source["from"], source["to"]
        if not _valid_date(start) or not _valid_date(end) or start > end:
            continue
        evidence = source["payload"].get(EVIDENCE_KEY)
        paid, reason = None, "legacy_without_group_payments"
        if isinstance(evidence, dict) and evidence.get("version") == 1:
            reason = "unreconciled_detail"
            if evidence.get("reason") == "reconciled":
                try:
                    paid = _cents(evidence.get("paid")) / 100
                    reason = "reconciled"
                except ValueError:
                    pass
        by_range[(start, end)] = {"from": start, "to": end, "paid": paid,
                                  "reason": reason}
    return [by_range[key] for key in sorted(by_range)]


def service_days(conn):
    """Read original normalized service rows, never their cash/report overlay."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_uploads'").fetchone():
        return []
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_uploads)")}
    if not {"data_date", "normalized_json", "services_sha256"} <= columns:
        return []
    result = []
    for day, payload_json, source_hash in conn.execute(
            "SELECT data_date,normalized_json,services_sha256 FROM daily_uploads ORDER BY data_date"):
        if not source_hash or not _valid_date(day):
            continue
        invalid = {"date": day, "services": None, "orders": None}
        try:
            payload = json.loads(payload_json)
            if not isinstance(payload, dict) or payload.get("data_date") != day:
                raise ValueError("invalid source day")
            items, retail = payload.get("items"), payload.get("retail_items", [])
            if not isinstance(items, list) or not isinstance(retail, list):
                raise ValueError("missing original rows")
            # A completed-only day, or a removed/cleared services layer, is
            # not affirmative evidence of zero ZTL orders/services.
            if not items and not retail:
                continue
            invoices, orders_complete = set(), True
            amount = 0
            for item in items:
                if not isinstance(item, dict) or item.get("date") != day:
                    raise ValueError("invalid service row")
                group = item.get("group")
                if not isinstance(group, str) or not group.strip():
                    raise ValueError("unknown service group")
                value = _cents(item.get("amount"))
                if not is_lab_group(group):
                    continue
                amount += value
                invoice = item.get("invoice")
                if (not isinstance(invoice, (str, int)) or isinstance(invoice, bool)
                        or not str(invoice).strip()):
                    orders_complete = False
                else:
                    # For this export data_date is the invoice's date.
                    # The same invoice under two providers still counts once.
                    invoices.add(str(invoice).strip())
            if any(not isinstance(item, dict) or item.get("date") != day for item in retail):
                raise ValueError("invalid retail row")
            if abs(amount) > 9007199254740991:
                raise ValueError("amount out of range")
            result.append({"date": day, "services": amount / 100,
                           "orders": len(invoices) if orders_complete else None})
        except (ValueError, TypeError, OverflowError):
            result.append(invalid)
    return result
