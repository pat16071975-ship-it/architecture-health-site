"""Pure, conservative proposals for incoming goods; no storage or posting.

Only identity/classification may be reused. Quantities and money always remain
the values extracted from the current source. A proposal never checks a page.
"""
import copy
import re
import unicodedata
from collections import defaultdict
from datetime import date
from statistics import median


def normalized(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", text))


_UNITS = {
    "шт": "шт", "штука": "шт", "штуки": "шт", "штук": "шт",
    "уп": "упак", "упак": "упак", "упаковка": "упак", "упаковки": "упак",
    "упаковок": "упак", "рул": "рул", "рулон": "рул", "рулона": "рул",
    "рулонов": "рул", "компл": "компл", "комплект": "компл", "к т": "компл",
    "набор": "набор", "наб": "набор", "пар": "пара", "пара": "пара",
    "пары": "пара", "кг": "кг", "килограмм": "кг", "л": "л", "литр": "л",
}


def normalized_unit(value):
    value = normalized(value)
    return _UNITS.get(value, value)


def _folder_paths(folders):
    """Resolve live names/parents, never a fixed numeric ID or ambiguous path."""
    by_id = {f.get("id"): f for f in folders if f.get("id") is not None}
    paths = defaultdict(set)
    for folder in folders:
        if folder.get("id") is None or not str(folder.get("name") or "").strip():
            continue
        names = [folder["name"]]
        seen = {folder["id"]}
        parent_id = folder.get("parent_id")
        if parent_id is not None:
            while parent_id is not None:
                if parent_id not in by_id or parent_id in seen:
                    names = []
                    break
                seen.add(parent_id)
                parent = by_id[parent_id]
                names.insert(0, parent["name"])
                parent_id = parent.get("parent_id")
        elif folder.get("parent"):
            names.insert(0, folder["parent"])
        if names:
            paths[tuple(normalized(n) for n in names)].add(folder["id"])
    return {path: next(iter(ids)) for path, ids in paths.items() if len(ids) == 1}


_SUPPLIES = "Расходные материалы"
_OFFICE = "Хоз/Канц товары"
# Rules describe commodity types, not suppliers, articles, document numbers,
# brands or item IDs. They select only a uniquely existing configured path.
_RULES = (
    (("Дезинфекция",), (
        r"\bдезинфицир\w*\b", r"\bдезинфек\w*\b",
        r"\bсалфетк\w*\b.*\bд\s+дез\b",
    )),
    ((_SUPPLIES, "ЦСО"), (
        r"\b(?:рулон|пакет|индикатор|бумаг)\w*\b.*\bстерилизац\w*\b",
        r"\bстерилизац\w*\b.*\b(?:рулон|пакет|индикатор|бумаг)\w*\b",
    )),
    ((_SUPPLIES, "СИЗ"), (
        r"\bбахил\w*\b",
        r"^(?!.*\bхозяйствен\w*\b).*\b(?:перчатк|маск|шапочк|респиратор)\w*\b.*\b(?:мед|медицинск|смотров|нитрил|стерил|однораз)\w*\b",
        r"^(?!.*\bхозяйствен\w*\b).*\b(?:медицинск|смотров|нитрил)\w*\b.*\b(?:перчатк|маск|шапочк)\w*\b",
    )),
    ((_SUPPLIES, "Гигиена"), (
        r"\bпалочк\w*\b.*\bватн\w*\b", r"\bватн\w*\b.*\bпалочк\w*\b",
    )),
    ((_SUPPLIES, "Разное"), (
        r"\bшприц\w*\b", r"\bэлектрод\w*\b.*\bэкг\b",
        r"\bпростын\w*\b.*\b(?:однораз\w*|[нh]\s+[сc])\b",
        r"\bшпател\w*\b.*\bтерапевтич\w*\b",
    )),
    ((_OFFICE, "Канц.Товары"), (
        r"\bпапка\s+регистратор\b", r"\bфайл(?:ы)?\s+вклад[а-я]*\b",
        r"\bролик\w*\b.*\bкасс\w*\b", r"\bкассов\w*\s+лент\w*\b",
        r"\bбумаг\w*\b.*\b(?:[аa]4|офисн\w*|принтер\w*|документ\w*)\b",
        r"\b(?:калькулятор|карандаш|ежедневник|скоросшивател)\w*\b",
        r"\bподставк\w*\b.*\bвизит\w*\b",
    )),
    ((_OFFICE, "Хоз.товары"), (
        r"\bмешк\w*\b.*\bмусор\w*\b",
        r"\bсредств\w*\b.*\bпрочистк\w*\b.*\bтруб\w*\b",
        r"\bгубк\w*\b.*\bмыть\w*\b.*\bпосуд\w*\b",
        r"\bавтоосвежител\w*\b", r"\bосвежител\w*\b.*\bвоздух\w*\b",
        r"\b(?:батарейк|вешалк)\w*\b", r"\bсалфетк\w*\b.*\bэкран\w*\b",
        r"\bпакет\w*\b.*\b(?:zip\s+lock|замком)\b",
        r"\bсалфетк\w*\b.*\b(?:бумажн|хозяйствен)\w*\b",
        r"\b(?:держател|насадк)\w*\b.*\bмоп\w*\b",
        r"\bкрем\w*\b.*\bрук\b", r"\bгел\w*\b.*\bстирк\w*\b",
        r"\bмыл\w*\b.*\bхозяйствен\w*\b",
        r"\bпокрыти\w*\b.*\bунитаз\w*\b",
    )),
    ((_OFFICE,), (r"\b(?:чай|кофе|молоко|сахар)\b",)),
)


def _history_rows(history):
    for entry in history:
        if not isinstance(entry, dict) or entry.get("checked") is not True:
            continue
        for row in entry.get("rows", [entry]):
            if isinstance(row, dict) and row.get("action") in ("new", "existing"):
                yield row


def enrich_rows(rows, items, folders, history=()):
    """Return copied proposals, with exact-match/history/semantic classification.

    ``history`` contains checked page dictionaries with ``rows``, or individual
    checked rows. Unchecked decisions never train this lookup. Existing choices
    are preserved, even if their referenced material/folder no longer exists.
    No quantities, costs, prices or checked flags are copied from history.
    """
    result = copy.deepcopy(rows)
    folder_ids = {f.get("id") for f in folders if f.get("id") is not None}
    paths = _folder_paths(folders)
    catalog = defaultdict(list)
    excluded = set()
    for item in items:
        if item.get("id") is None:
            continue
        name, unit = normalized(item.get("name")), normalized_unit(item.get("unit"))
        if name and unit:
            if item.get("excluded"):
                excluded.add((name, unit))
            else:
                catalog[(name, unit)].append(item)
    learned = defaultdict(set)
    for row in _history_rows(history):
        name, unit = normalized(row.get("name")), normalized_unit(row.get("unit"))
        if name and unit:
            # Retain stale choices as conflicting evidence; silently ignoring a
            # removed folder could make another old choice look unanimous.
            learned[(name, unit)].add(row.get("folder_id"))
    for row in result:
        if (row.get("action", "unresolved") != "unresolved"
                or row.get("item_id") is not None or row.get("folder_id") is not None):
            continue
        name, unit = normalized(row.get("name")), normalized_unit(row.get("unit"))
        if not name:
            continue
        key = (name, unit)
        matches = catalog.get(key, []) if unit else []
        if matches:
            if len(matches) == 1:
                item = matches[0]
                row["classification"] = {"source": "catalog_exact", "original_unit": row.get("unit", "")}
                row.update(action="existing", item_id=item["id"], unit=item["unit"],
                           folder_id=item.get("folder_id") if item.get("folder_id") in folder_ids else None)
            else:
                row["classification"] = {"source": "ambiguous_catalog"}
            # Ambiguous exact material identity must not be disguised by a
            # semantic new-material proposal.
            continue
        if key in excluded:
            row["classification"] = {"source": "excluded_catalog"}
            continue
        previous = learned.get(key, set()) if unit else set()
        if previous:
            if len(previous) == 1 and next(iter(previous)) in folder_ids:
                row.update(action="new", folder_id=next(iter(previous)))
                row["classification"] = {"source": "checked_history"}
            else:
                row["classification"] = {"source": "ambiguous_history"}
            continue
        matched_paths = {
            tuple(normalized(part) for part in path)
            for path, patterns in _RULES
            if any(re.search(pattern, name) for pattern in patterns)
        }
        if len(matched_paths) == 1:
            path = next(iter(matched_paths))
            if path in paths:
                row.update(action="new", folder_id=paths[path])
                row["classification"] = {"source": "commodity_rule", "path": list(path)}
        elif len(matched_paths) > 1:
            row["classification"] = {"source": "ambiguous_commodity"}
    return result


_DATE = re.compile(r"(?<!\d)(\d{2})[.]\s*(\d{2})[.]\s*(\d{4})(?!\d)")
_ENTITY = re.compile(r"\b(?P<form>[ОO0]{3}|ПАО|ОАО|ЗАО|АО)\s*[«\"](?P<name>[^»\"\n]{1,150})[»\"]", re.I)
_ROLE_AFTER = re.compile(r"далее\s+по\s+тексту\s*[«\"]?(?:поставщик|продавец)\b", re.I)
_ROLE_BEFORE = re.compile(r"\b(?:поставщик|продавец|грузоотправитель)\b", re.I)
_OTHER_PARTY = re.compile(r"\b(?:покупатель|грузополучатель|заказчик)\b", re.I)
_LEGAL_FORM = re.compile(r"\b(?:[ОO0]{3}|ПАО|ОАО|ЗАО|АО|ИП)\b", re.I)


def _source_lines(source, lines):
    if isinstance(source, dict):
        # The image reader keeps a second, complete line map in the deskewed
        # frame. Original-frame word boxes remain the row evidence, but their
        # Y coordinates can interleave neighboring supplier/buyer sentences.
        aligned = source.get("metadata_lines")
        lines = aligned if isinstance(aligned, list) and aligned else source.get("lines", lines)
        source = source.get("words", [])
    if lines is None and isinstance(source, list) and (not source or isinstance(source[0], dict)):
        lines = source
    if lines is not None:
        return [{"line": line.get("id", i), "text": str(line.get("text", ""))}
                for i, line in enumerate(lines, 1) if isinstance(line, dict)]
    words = [w for w in source or [] if isinstance(w, (list, tuple)) and len(w) == 6]
    if not words:
        return []
    tolerance = max(4, min(18, median(w[3] for w in words) * .55))
    grouped = []
    for word in sorted(words, key=lambda w: (w[1] + w[3] / 2, w[0])):
        center = word[1] + word[3] / 2
        if not grouped or abs(center - grouped[-1][0]) > tolerance:
            grouped.append((center, [word]))
        else:
            grouped[-1][1].append(word)
    return [{"line": i, "text": " ".join(w[5] for w in sorted(group, key=lambda w: w[0]))}
            for i, (_, group) in enumerate(grouped, 1)]


def _title_kind(text):
    text = re.sub(r"^[\s|•*\-:ХхXx]{0,12}", "", text).casefold().replace("ё", "е")
    if re.match(r"(?:упд\b|универсальн\w*\s+передаточн\w*\s+документ\b)", text):
        return "upd"
    if re.match(r"счет\s*[-–—]?\s*фактура\b", text):
        return "invoice"
    if re.match(r"(?:товарн\w*\s+)?накладная\b", text):
        return "waybill"
    if re.match(r"акт(?:\s*(?:№|#)|\s+(?:приема|выполненных|оказанных|сдачи)\b|\s*$)", text):
        return "act"
    if re.match(r"счет(?:\s*(?:№|#)|\s+на\s+оплату\b|\s*$)", text):
        return "bill"
    return None


def _number(text):
    marker = re.search(r"(?:№|#|\bN(?:o)?[.]?)\s*", text, re.I)
    tail = text[marker.end():] if marker else ""
    if not marker:
        # A repeated act/footer often omits the number sign.
        footer = re.search(r"\bакт\s+при[её]ма[\s-]+передачи\s+", text, re.I)
        tail = text[footer.end():] if footer else ""
    if not tail:
        return None
    tail = re.split(r"\s+от\b", tail, maxsplit=1, flags=re.I)[0].strip()
    match = re.match(r"([^\s,;:]+)", tail)
    if not match:
        return None
    value = match.group(1)
    # Join only split long numeric identifiers, never a neighboring date.
    if re.fullmatch(r"[A-Za-zА-Яа-яЁё$]?\d{5,}", value):
        extra = re.match(r"(?:\s+\d{3,})*", tail[match.end():]).group(0)
        value += extra.replace(" ", "")
    if "$" in value or not re.search(r"\d", value) or len(value) > 100:
        return ""
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9./\\_-]*", value):
        return ""
    if _DATE.fullmatch(value):
        return None
    return value


def _dates(text):
    result = []
    for match in _DATE.finditer(text):
        try:
            value = date(int(match[3]), int(match[2]), int(match[1])).isoformat()
            if value not in result:
                result.append(value)
        except ValueError:
            continue
    return result


def header_proposal(source=None, lines=None, page=1):
    """Propose source-backed identity; uncertainty stays explicit and unchecked.

    Accept a page dictionary, word boxes, or line dictionaries. A page's
    ``metadata_lines`` is the image reader's complete deskewed line map, with
    its footer and repeated dates; it takes precedence over raw ``lines``.
    ``evidence``
    records source line text; no extracted value is a confirmation. Dollar/5/S
    substitutions, neighboring order dates and first-company guesses are not
    used to manufacture document identity.
    """
    records = _source_lines(source, lines)
    proposal = {"document_type": "unknown", "counterparty": "", "number": "",
                "date": None, "part": page, "note": "", "evidence": {},
                "issues": [], "pagination": None}
    titles = [(i, _title_kind(r["text"])) for i, r in enumerate(records[:20])]
    titles = [(i, kind) for i, kind in titles if kind]
    if titles:
        first, kind = titles[0]
        proposal["document_type"] = kind
        proposal["evidence"]["document_type"] = [records[first]]
        occurrences = [i for i, r in enumerate(records) if _title_kind(r["text"]) == kind]
        numbers, dates = set(), set()
        bad_number = False
        for i in occurrences:
            current = records[i]
            value = _number(current["text"])
            if value is not None:
                proposal["evidence"].setdefault("number", []).append(current)
                if value:
                    numbers.add(value)
                else:
                    bad_number = True
            # A footer may include both document and order dates. Only the
            # portion before the order-reference label belongs to this title.
            window = []
            for record in records[i:i + (5 if i == first else 3)]:
                text = re.split(r"\b(?:по\s+(?:[з3][\s-]*[сc]|\d+)|заказ\w*)\b", record["text"], maxsplit=1, flags=re.I)[0]
                window.append({**record, "text": text})
                if _dates(text):
                    break
                if re.search(r"\b(?:ИНН|покупатель|поставщик)\b", text, re.I):
                    break
            for record in window:
                found = _dates(record["text"])
                if found:
                    dates.update(found[:1])
                    proposal["evidence"].setdefault("date", []).append(record)
        if len(numbers) == 1 and not bad_number:
            proposal["number"] = next(iter(numbers))
        elif numbers or bad_number:
            proposal["issues"].append("Номер документа требует повторного чтения.")
        if len(dates) == 1:
            proposal["date"] = next(iter(dates))
        elif len(dates) > 1:
            proposal["issues"].append("Распознаны разные даты одного документа.")
    joined = "\n".join(r["text"] for r in records)
    entities = list(_ENTITY.finditer(joined))
    suppliers = set()
    for i, entity in enumerate(entities):
        before = joined[max(0, entity.start() - 160):entity.start()]
        after_end = entities[i + 1].start() if i + 1 < len(entities) else len(joined)
        after = joined[entity.end():min(after_end, entity.end() + 160)]
        roles = list(_ROLE_BEFORE.finditer(before))
        role = roles[-1] if roles else None
        role_before = bool(role and not _OTHER_PARTY.search(before[role.end():])
                           and not _ENTITY.search(before[role.end():]))
        after_role = _ROLE_AFTER.search(after)
        role_after = bool(after_role and not _OTHER_PARTY.search(after[:after_role.start()])
                          and not _LEGAL_FORM.search(after[:after_role.start()]))
        if role_before or role_after:
            form = entity["form"]
            if re.fullmatch(r"[ОO0]{3}", form, re.I):
                form = "ООО"
            name = " ".join(entity["name"].split())
            if normalized(name) in ("сторона", "стороны", "покупатель", "поставщик", "продавец", "заказчик"):
                continue
            suppliers.add(f"{form.upper()} «{name}»")
            proposal["evidence"].setdefault("counterparty", []).append(
                {"line": None, "text": (before[-100:] + entity[0] + after[:100]).strip()})
    if len(suppliers) == 1:
        proposal["counterparty"] = next(iter(suppliers))
    elif len(suppliers) > 1:
        proposal["issues"].append("Поставщик распознан неоднозначно.")
    pagination = set()
    for match in re.finditer(r"\bстраниц[аы]\s*(\d{1,3})\s+из\s+(\d{1,3})\b", joined, re.I):
        current, total = map(int, match.groups())
        if 1 <= current <= total <= 200:
            pagination.add((current, total))
            proposal["evidence"].setdefault("pagination", []).append({"line": None, "text": match[0]})
    if len(pagination) == 1:
        current, total = next(iter(pagination))
        proposal["pagination"] = {"page": current, "of": total}
    elif len(pagination) > 1:
        proposal["issues"].append("Нумерация страниц документа неоднозначна.")
    if isinstance(source, dict) and isinstance(source.get("metadata_first_pass"), list):
        # Both reads concern the same original PDF, never another document or
        # a manually corrected answer. A confident-looking second read may
        # still misread a year or a page digit. Keep those identity fields
        # unknown unless both independent reads supply the same value.
        first = header_proposal(lines=source["metadata_first_pass"], page=page)
        messages = {
            "date": "Дата не подтверждена двумя чтениями оригинала.",
            "pagination": "Нумерация страниц не подтверждена двумя чтениями оригинала.",
        }
        for field, message in messages.items():
            aligned_value, first_value = proposal[field], first[field]
            combined = [{**entry, "read": "aligned"}
                        for entry in proposal["evidence"].get(field, [])]
            combined.extend({**entry, "read": "first_pass"}
                            for entry in first["evidence"].get(field, []))
            proposal["evidence"][field] = combined
            proposal["evidence"][field + "_cross_read"] = {
                "aligned": aligned_value, "first_pass": first_value,
            }
            if aligned_value is None or first_value is None or aligned_value != first_value:
                proposal[field] = None
                proposal["issues"].append(message)
    return proposal
