"""Read-only provenance projection for independently uploaded management sources.

Storage rows are NEVER changed here. Cash can be newer than completed visits;
carry only already-confirmed cumulative CLINICAL metrics into the presentation
of a later cash-only date, explicitly reporting the two source-as-of dates.
"""
import copy
import re

_DAY = re.compile(r"^20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$")

# Only these values are verified cumulative clinical/service source fields.
# Cash Fact, legal entity splits, providers' cash revenue and paid MIS snapshots
# must ALWAYS remain owned by their own sources on their own dates.
_CLINICAL_CARRY = (
    "primary", "repeat", "dentPrimary", "dentRepeat",
    "clinicPrimary", "clinicRepeat", "labOrders", "_uploadControl",
    "grossRevenue", "discountAmount", "discountDataComplete",
    "billedMedicine", "billedLab", "billedDentists", "billedClinicDocs",
    "billedLabRevenue",
)


def _has_clinical(record):
    if not isinstance(record, dict):
        return False
    return any(name in record for name in (
        "_uploadControl", "primary", "repeat", "dentPrimary",
        "dentRepeat", "clinicPrimary", "clinicRepeat", "labOrders",
    ))


_CLONED_CLINICAL_FIELDS = (
    "_source", "_aggregation", "_uploadControl", "primary", "repeat",
    "dentPrimary", "dentRepeat", "clinicPrimary", "clinicRepeat", "labOrders",
    "grossRevenue", "discountAmount", "discountDataComplete",
)


def copied_cash_clinical_source(record, date, previous):
    """Return source day for a proven cash-forward clinical copy, else None.

    Explicit markers are authoritative for new copies. Legacy unmarked copies
    qualify only when their clinical identity and cumulative fields match an
    earlier known record exactly. Unproven differences stay clinical/guarded.
    'previous' is an iterable of (date, real clinical record), newest first.
    """
    if not isinstance(record, dict) or record.get("_cash_rule") != "positive-receipts-only-v1":
        return None

    explicit = str(record.get("_clinicalAsOf") or "")
    if record.get("_cashForwardClone") is True:
        return explicit if explicit and explicit < date else ""
    if explicit:
        return explicit if explicit < date else None

    source_name = record.get("_source")
    if not isinstance(source_name, str) or not source_name:
        return None
    for prior_date, earlier in previous:
        if prior_date >= date or not isinstance(earlier, dict):
            continue
        if earlier.get("_source") != source_name:
            continue
        if not any(k in earlier for k in ("primary", "repeat", "_uploadControl")):
            continue
        if all(
            (key in record) == (key in earlier)
            and (key not in earlier or record[key] == earlier[key])
            for key in _CLONED_CLINICAL_FIELDS
        ):
            return str(earlier.get("_clinicalAsOf") or prior_date)
    return None


def project_for_reports(records):
    """Make a detached as-of view of {ISO-date: stored-report} for read-only UI.

    If 09 Oct has only the cash source and completed visits end on 07 Oct,
    the view of 09 Oct carries the 07 Oct clinical MTD with clinicalAsOf=07,
    while keeping original 09 Oct cash fields with cashAsOf=09.
    This is source-aware presentation, never an inserted or updated DB row.
    """
    result = {}
    last_clinical = {}
    clinical_sources = {}
    for data_date in sorted(records):
        if not _DAY.fullmatch(str(data_date)):
            continue
        stored = records[data_date]
        if not isinstance(stored, dict):
            continue
        month = data_date[:7]
        view = copy.deepcopy(stored)
        view["date"] = data_date
        previous_sources = list(reversed(clinical_sources.get(month, [])))
        forwarded = copied_cash_clinical_source(stored, data_date, previous_sources)
        has_clinical = _has_clinical(stored) and forwarded is None
        if has_clinical:
            observed_as_of = str(stored.get("_clinicalAsOf") or data_date)
            if not _DAY.fullmatch(observed_as_of) or observed_as_of[:7] != month or observed_as_of > data_date:
                raise ValueError(f"Неверная дата источника приёмов за {data_date}.")
            view["_clinicalAsOf"] = observed_as_of
            last_clinical[month] = (observed_as_of, view)
            clinical_sources.setdefault(month, []).append((data_date, stored))
        else:
            previous = last_clinical.get(month)
            if previous is not None:
                prior_day, prior = previous
                for name in _CLINICAL_CARRY:
                    # A forward cash clone may contain old clinical numbers.
                    # Replace them with the latest independently verified MTD.
                    if name in prior and (forwarded is not None or name not in view):
                        view[name] = copy.deepcopy(prior[name])
                view["_clinicalAsOf"] = prior_day
                if "_serviceAsOf" in prior:
                    view["_serviceAsOf"] = str(prior["_serviceAsOf"])
            else:
                view["_clinicalAsOf"] = ""
        if view.get("_cash_rule") == "positive-receipts-only-v1":
            view["_cashAsOf"] = str(stored.get("_cashAsOf") or data_date)
        result[data_date] = view
    return result
