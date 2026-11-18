"""Read-only doctor amounts from the ORIGINAL service export, never cash/paid."""
import json
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation


def _name(value):
    return re.sub(r"\s+", "", str(value or "")).casefold().replace("ё", "е")


def service_days(conn, mappings, ignored):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_uploads'").fetchone():
        return []
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_uploads)")}
    if not {"data_date", "normalized_json", "services_sha256"} <= columns:
        return []
    aliases = {}
    for direction, providers in mappings.items():
        for source, display in providers.items():
            for alias in (source, display):
                aliases.setdefault(_name(alias), set()).add((direction, display))
    ignored = {_name(value) for value in ignored}
    result = []
    for row in conn.execute("SELECT data_date,normalized_json,services_sha256 FROM daily_uploads ORDER BY data_date"):
        day, raw, source_hash = row
        # A completed-visits-only record is not evidence of zero services.
        if not source_hash:
            continue
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("data_date") != day:
                continue
            if not isinstance(payload.get("items"), list) or not isinstance(payload.get("retail_items", []), list):
                continue
        except (TypeError, ValueError):
            continue
        totals = {"dent": {}, "structure": {}, "lab": {}}
        complete = True
        for item in payload["items"] + payload.get("retail_items", []):
            if not isinstance(item, dict) or item.get("date") != day:
                complete = False
                continue
            key = _name(item.get("staff"))
            targets = aliases.get(key, set())
            if not targets and key in ignored:
                continue
            if len(targets) != 1:
                complete = False
                continue
            try:
                value = item.get("amount")
                if value is None or isinstance(value, bool):
                    raise ValueError()
                amount = Decimal(str(value))
                if not amount.is_finite() or not math.isfinite(float(amount)):
                    raise ValueError()
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                complete = False
                continue
            direction, display = next(iter(targets))
            totals[direction][display] = totals[direction].get(display, Decimal(0)) + amount
        result.append({
            "date": day, "complete": complete,
            **{field: {name: float(value.quantize(Decimal("0.01"))) for name, value in totals[kind].items()}
               for kind, field in (("dent", "dentists"), ("structure", "clinicDocs"))},
        })
    return result
