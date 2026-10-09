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


def project_for_reports(records):
    """Make a detached as-of view of {ISO-date: stored-report} for read-only UI.

    If 09 Oct has only the cash source and completed visits end on 07 Oct,
    the view of 09 Oct carries the 07 Oct clinical MTD with clinicalAsOf=07,
    while keeping original 09 Oct cash fields with cashAsOf=09.
    This is source-aware presentation, never an inserted or updated DB row.
    """
    result = {}
    last_clinical = {}
    for data_date in sorted(records):
        if not _DAY.fullmatch(str(data_date)):
            continue
        stored = records[data_date]
        if not isinstance(stored, dict):
            continue
        month = data_date[:7]
        view = copy.deepcopy(stored)
        view["date"] = data_date
        has_clinical = _has_clinical(stored)
        if has_clinical:
            observed_as_of = str(stored.get("_clinicalAsOf") or data_date)
            if not _DAY.fullmatch(observed_as_of) or observed_as_of[:7] != month or observed_as_of > data_date:
                raise ValueError(f"Неверная дата источника приёмов за {data_date}.")
            view["_clinicalAsOf"] = observed_as_of
            last_clinical[month] = (observed_as_of, view)
        else:
            previous = last_clinical.get(month)
            if previous is not None:
                prior_day, prior = previous
                for name in _CLINICAL_CARRY:
                    if name not in view and name in prior:
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
