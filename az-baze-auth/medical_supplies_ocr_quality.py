"""Pure checks of OCR drafts; no reads, writes, posting or auto-approval.

The printed total is supplied by the image/text extractor with its column
basis. It is never guessed from the last number on a page. Comparing totals
includes every visible goods row, even a row marked ``skip``: excluding one
would change the meaning of the original document's total. Such exclusions
are reported separately. Matching totals are only one check, not proof that
quantities, names or document completeness are correct.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re


MAX_ROWS = 200
MAX_PAGES = 20
MAX_VALUE = Decimal("1000000000")
TOTAL_BASES = frozenset(("printed_gross", "printed_single_total"))
IDENTITY_FIELDS = ("document_type", "counterparty", "number", "date")


def _decimal(value, scale, maximum=MAX_VALUE):
    """Parse bounded decimal evidence without binary floats or rounding."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value).strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not text or len(text) > 40:
        return None
    if " " in text:
        if not re.fullmatch(r"[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]{1,8})?", text):
            return None
        text = text.replace(" ", "")
    if not re.fullmatch(r"[0-9]{1,14}(?:[.,][0-9]{1,8})?", text):
        return None
    try:
        number = Decimal(text.replace(",", "."))
        if (not number.is_finite() or number > maximum
                or number * scale != (number * scale).to_integral_value()):
            return None
        return number
    except InvalidOperation:
        return None


def _present(value):
    return isinstance(value, str) and bool(value.strip())


def _unique(messages):
    return list(dict.fromkeys(messages))


def _source_issues(data):
    """Retain bounded, already-generated diagnostic messages, not raw text."""
    messages = []
    groups = [data.get("recognition_issues", [])]
    # A saved human header supersedes its old proposal, including deliberate
    # blank fields. Never recycle a previous summarize_page() result here:
    # otherwise a corrected total would keep its old mismatch warning.
    if "header" not in data:
        value = data.get("header_candidate")
        if isinstance(value, dict):
            groups.append(value.get("issues", []))
    for group in groups:
        if isinstance(group, (list, tuple)):
            messages.extend(message.strip() for message in group[:40]
                            if isinstance(message, str) and 0 < len(message.strip()) <= 500)
    return _unique(messages)


def summarize_page(data, rows):
    """Return draft completeness and a strict comparison with a printed total.

    ``complete_rows`` is the number of rows having a name, unit, positive
    quantity and valid nonnegative amount. Category selection is independent
    of the money comparison and is reported without changing any row.
    ``total_matches`` stays ``None`` whenever either side is unconfirmed.
    """
    data = data if isinstance(data, dict) else {}
    issues = _source_issues(data)
    result = {"issues": issues, "total_expected": None, "total_actual": None,
              "total_matches": None, "complete_rows": 0, "count": 0}
    if not isinstance(rows, (list, tuple)):
        issues.append("Не удалось прочитать товарные строки. Повторите разбор страницы.")
        return result
    result["count"] = len(rows)
    if len(rows) > MAX_ROWS:
        issues.append("На странице больше 200 товарных строк. Разделите документ и повторите разбор.")
        return result

    expected = None
    total = data.get("printed_total")
    if total not in (None, ""):
        if data.get("total_basis") not in TOTAL_BASES:
            issues.append("Не подтверждено назначение печатного итога. Проверьте, что это стоимость с налогом.")
        else:
            expected = _decimal(total, 100, MAX_VALUE * MAX_ROWS)
            if expected is None:
                issues.append("Печатный итог прочитан неоднозначно. Проверьте сумму по оригиналу.")
    elif rows:
        issues.append("Печатный итог не распознан. Сумму строк пока нельзя сверить с документом.")
    if expected is not None:
        result["total_expected"] = format(expected, ".2f")

    if not rows:
        if not data.get("no_items"):
            issues.append("Товарные строки не найдены. Проверьте, есть ли на странице таблица товаров.")
        if expected is not None and expected != 0:
            issues.append("В документе указан ненулевой итог, но товарные строки не найдены.")
        result["issues"] = _unique(issues)
        return result

    sums = []
    missing = defaultdict(list)
    skipped = []
    unresolved_categories = []
    uncertain_basis = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            missing["строку целиком"].append(index)
            continue
        quantity = _decimal(row.get("quantity"), 1000)
        amount = _decimal(row.get("value"), 100)
        fields = []
        if not _present(row.get("name")):
            fields.append("название")
        if not _present(row.get("unit")):
            fields.append("единицу")
        if quantity is None or quantity <= 0:
            fields.append("количество")
        if amount is None:
            fields.append("стоимость")
        basis = row.get("value_basis")
        if basis is not None and basis not in TOTAL_BASES:
            uncertain_basis.append(index)
            if "стоимость" not in fields:
                fields.append("стоимость")
        for field in fields:
            missing[field].append(index)
        if not fields:
            result["complete_rows"] += 1
            sums.append(amount)
        if row.get("action") == "skip":
            skipped.append(index)
        elif row.get("folder_id") in (None, ""):
            unresolved_categories.append(index)

    for field, numbers in missing.items():
        label = ", ".join(str(n) for n in numbers[:12])
        if len(numbers) > 12:
            label += " и ещё " + str(len(numbers) - 12)
        issues.append("Проверьте " + field + " в строках: " + label + ".")
    if uncertain_basis:
        issues.append("В отдельных строках не подтверждена стоимость с налогом. Сумма без налога или НДС не подставляется вместо неё.")
    if unresolved_categories:
        issues.append("Не выбран раздел для " + str(len(unresolved_categories)) + " товарных строк.")
    if skipped:
        issues.append("Есть исключённые строки: " + str(len(skipped)) + ". В сверке с печатным итогом учитываются все строки документа.")
    if result["complete_rows"] == len(rows):
        actual = sum(sums, Decimal("0"))
        result["total_actual"] = format(actual, ".2f")
        if expected is not None:
            result["total_matches"] = actual == expected
            if actual != expected:
                issues.append("Сумма строк " + format(actual, ".2f")
                              + " ₽ не совпадает с печатным итогом "
                              + format(expected, ".2f") + " ₽. Проверьте пропуски и суммы товаров.")
    result["issues"] = _unique(issues)
    return result


def _identity(header):
    if not isinstance(header, dict):
        return None
    if any(not _present(header.get(key)) or len(header[key]) > 200
           for key in IDENTITY_FIELDS):
        return None
    if header["document_type"] == "unknown":
        return None
    try:
        if date.fromisoformat(header["date"]).isoformat() != header["date"]:
            return None
    except (TypeError, ValueError):
        return None
    # Case/spacing and typographic quotes do not change an identity. Do not
    # remove punctuation from document numbers or replace ambiguous glyphs.
    return tuple(" ".join(header[key].strip().casefold().replace("ё", "е")
                          .replace("«", '"').replace("»", '"').split())
                 for key in IDENTITY_FIELDS)


def _page_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= MAX_PAGES:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,2}", value):
        number = int(value)
        return number if 1 <= number <= MAX_PAGES else None
    return None


def document_issues(pages):
    """Report incomplete/duplicate pagination only within proven identities.

    Accept the existing ``state['pages']`` mapping or a sequence of page data.
    Independent acts in one PDF never fill gaps in each other's pagination.
    A human ``header`` has priority, including an intentionally empty header;
    pagination still comes from the extractor's ``header_candidate`` when a
    manual header does not contain that evidence.
    """
    if isinstance(pages, dict):
        entries = list(pages.items())
    elif isinstance(pages, (list, tuple)):
        entries = list(enumerate(pages, 1))
    else:
        return ["Не удалось проверить комплектность документа: страницы не прочитаны."]
    if len(entries) > MAX_PAGES:
        return ["В документе больше 20 страниц. Комплектность автоматически не проверена."]
    groups = defaultdict(list)
    issues = []
    for index, (key, data) in enumerate(entries, 1):
        pdf_page = _page_number(key) or index
        if not isinstance(data, dict):
            issues.append("Страница PDF " + str(pdf_page) + ": данные разбора недоступны.")
            continue
        proposal = data.get("header_candidate")
        proposal = proposal if isinstance(proposal, dict) else {}
        header = data["header"] if "header" in data else proposal
        pagination = (header.get("pagination") if isinstance(header, dict) else None)
        if pagination is None:
            pagination = proposal.get("pagination", data.get("pagination"))
        identity = _identity(header)
        if pagination is not None:
            current = _page_number(pagination.get("page")) if isinstance(pagination, dict) else None
            total = _page_number(pagination.get("of")) if isinstance(pagination, dict) else None
            if current is None or total is None or current > total:
                issues.append("Страница PDF " + str(pdf_page)
                              + ": нумерация страниц распознана неоднозначно. Проверьте оригинал.")
                continue
            if identity is None:
                issues.append("Страница PDF " + str(pdf_page)
                              + ": реквизиты распознаны не полностью. Нельзя проверить, какие страницы относятся к одному документу.")
                continue
            groups[identity].append((pdf_page, current, total))
        elif identity is not None:
            groups[identity].append((pdf_page, None, None))
        elif not data.get("no_items"):
            issues.append("Страница PDF " + str(pdf_page)
                          + ": реквизиты и нумерация не прочитаны полностью. Комплектность не подтверждена.")

    for group in groups.values():
        labelled = [(pdf_page, current, total) for pdf_page, current, total in group
                    if current is not None]
        pdf_label = ", ".join(str(n[0]) for n in group)
        if not labelled:
            issues.append("Страницы PDF " + pdf_label
                          + ": не прочитана нумерация. Комплектность не подтверждена.")
            continue
        totals = {n[2] for n in labelled}
        if len(totals) != 1:
            issues.append("Страницы PDF " + pdf_label
                          + ": у одного документа указано разное число страниц. Проверьте комплектность.")
            continue
        total = next(iter(totals))
        printed = [n[1] for n in labelled]
        repeated = sorted({n for n in printed if printed.count(n) > 1})
        if repeated:
            issues.append("Страницы PDF " + pdf_label + ": повторяются номера страниц документа "
                          + ", ".join(map(str, repeated)) + ". Проверьте, не загружен ли дубль.")
        if len(labelled) != len(group):
            issues.append("Страницы PDF " + pdf_label
                          + ": на части страниц не прочитана нумерация. Комплектность не подтверждена.")
        missing = sorted(set(range(1, total + 1)) - set(printed))
        if missing:
            issues.append("Страницы PDF " + pdf_label + ": в документе не найдены страницы "
                          + ", ".join(map(str, missing)) + " из " + str(total)
                          + ". Добавьте недостающие страницы оригинала.")
    return _unique(issues)
