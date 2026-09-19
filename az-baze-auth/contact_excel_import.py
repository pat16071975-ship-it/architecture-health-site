import io
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

EXPECTED_HEADERS = ["#", "Наименование", "По каким вопросам обращаться", "ФИО", "Номер телефона"]
MAX_XLSX_BYTES = 10 * 1024 * 1024


class ContactImportError(ValueError):
    pass


class ContactImportConflict(ContactImportError):
    pass


def _clean(value):
    if value is None:
        return ""
    value = str(value).replace("\t", " ").replace('""', '"').strip()
    value = re.sub(r"[ \u00a0]+", " ", value)
    value = re.sub(r"\n\s*·\s*", "\n• ", value)
    value = value.replace('.)"', ".)").rstrip('"').strip()
    return value


def _norm(value):
    return re.sub(r"\s+", " ", _clean(value)).casefold()


def _normalize_phone(value):
    raw = _clean(value)
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw


def _split_name_role(value):
    value = _clean(value)
    match = re.match(r"^(.*?)\s*[-–—]\s*(.+)$", value)
    if not match:
        return value, ""
    name = match.group(1).strip()
    role = match.group(2).strip()
    if role:
        role = role[0].upper() + role[1:]
    return name, role


def _column_index(cell_ref):
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - 64)
    return result - 1


def _read_sheet_rows(data, sheet_name):
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ContactImportError("Файл не похож на корректный Excel .xlsx.") from exc

    with zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{ns_main}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns_main}}}t")))

        try:
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        except KeyError as exc:
            raise ContactImportError("В Excel-файле не найдена структура книги.") from exc

        rid = None
        sheets = wb_root.find(f"{{{ns_main}}}sheets")
        if sheets is not None:
            for sheet in sheets:
                if sheet.attrib.get("name") == sheet_name:
                    rid = sheet.attrib.get(f"{{{ns_rel}}}id")
                    break
        if not rid:
            raise ContactImportError(f"В книге нет листа «{sheet_name}».")

        target = None
        for rel in rel_root.findall(f"{{{ns_pkg}}}Relationship"):
            if rel.attrib.get("Id") == rid:
                target = rel.attrib.get("Target")
                break
        if not target:
            raise ContactImportError("Не удалось открыть лист «Все контакты».")

        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        try:
            ws_root = ET.fromstring(zf.read(target))
        except KeyError as exc:
            raise ContactImportError("Файл листа «Все контакты» отсутствует.") from exc

        sheet_data = ws_root.find(f"{{{ns_main}}}sheetData")
        if sheet_data is None:
            return []

        rows = []
        for row in sheet_data.findall(f"{{{ns_main}}}row"):
            values = {}
            max_col = -1
            for cell in row.findall(f"{{{ns_main}}}c"):
                idx = _column_index(cell.attrib.get("r", "A1"))
                max_col = max(max_col, idx)
                typ = cell.attrib.get("t")
                value = ""
                if typ == "inlineStr":
                    inline = cell.find(f"{{{ns_main}}}is")
                    if inline is not None:
                        value = "".join(t.text or "" for t in inline.iter(f"{{{ns_main}}}t"))
                else:
                    raw = cell.find(f"{{{ns_main}}}v")
                    if raw is not None and raw.text is not None:
                        if typ == "s":
                            try:
                                value = shared[int(raw.text)]
                            except (ValueError, IndexError):
                                value = ""
                        else:
                            value = raw.text
                values[idx] = value
            if max_col >= 0:
                rows.append([values.get(i, "") for i in range(max_col + 1)])
        return rows


def _map_contact(label, responsibility, full_name, phone, current_group):
    label = _clean(label)
    responsibility = _clean(responsibility)
    full_name, embedded_role = _split_name_role(full_name)
    organization = ""
    role = embedded_role
    new_group = current_group

    if label:
        low = label.casefold()
        if label.strip().upper() == "IT":
            organization, new_group = "IT", "IT"
        elif label == "Пилигрим":
            organization, new_group = "Пилигрим", "Пилигрим"
            if responsibility.casefold().startswith("управляющий"):
                role = role or "Управляющий"
        elif low.startswith("юрист"):
            role, new_group = label, ""
        elif low == "патент":
            role, new_group = "Патент", ""
        elif "пожар" in low or "охрана труда" in low:
            role, new_group = "Пожарная безопасность / охрана труда", ""
        elif "атмосфера" in low:
            organization, role, new_group = 'Типография "Атмосфера"', "Типография", ""
        elif "шрих-м" in low:
            organization, role, new_group = 'Компания "ШРИХ-М"', "Ремонт касс", ""
        elif "diers" in low:
            organization, role, new_group = "DIERS", "Специалист по установке", ""
        elif "фотобум" in low:
            organization, role, new_group = "Компания «ФОТОБУМ»", "Ремонт / диагностика фототехники", ""
        elif "fdoc" in low:
            organization, role, new_group = "FDoc", "Сервис электронного подписания и обмена документами", ""
        elif low == "1с":
            organization, new_group = "1С", ""
        else:
            organization, new_group = label, label
    elif current_group:
        organization = current_group
        lowr = responsibility.casefold()
        if current_group == "Пилигрим":
            if lowr.startswith("юрист собственников"):
                role = role or "Юрист собственников"
            elif lowr.startswith("администратор на ресепшен"):
                role = role or "Администратор ресепшен"

    return {
        "organization": organization,
        "organization_role": role,
        "full_name": full_name,
        "phone": _normalize_phone(phone),
        "responsibility": responsibility,
    }, new_group


def extract_contacts(data):
    if not data:
        raise ContactImportError("Файл пуст.")
    if len(data) > MAX_XLSX_BYTES:
        raise ContactImportError("Файл слишком большой. Максимум 10 МБ.")

    rows = _read_sheet_rows(data, "Все контакты")
    if not rows:
        raise ContactImportError("Лист «Все контакты» пуст.")

    headers = [_clean(v) for v in (rows[0] + [""] * 5)[:5]]
    if headers != EXPECTED_HEADERS:
        raise ContactImportError(
            "Неожиданные колонки листа «Все контакты». Нужны: " + ", ".join(EXPECTED_HEADERS)
        )

    contacts = []
    skipped = 0
    current_group = ""
    for row in rows[1:]:
        row = list(row) + [""] * (5 - len(row))
        _number, label, responsibility, full_name, phone = row[:5]
        meaningful = [_clean(v) for v in (responsibility, full_name, phone)]
        if not any(_clean(v) for v in (label, responsibility, full_name, phone)):
            continue
        # A row with only a heading/name but no contact details is not imported.
        if _clean(label) and not any(meaningful):
            skipped += 1
            current_group = _clean(label)
            continue
        contact, current_group = _map_contact(label, responsibility, full_name, phone, current_group)
        contacts.append(contact)

    if not contacts:
        raise ContactImportError("На листе «Все контакты» не найдено контактов для переноса.")
    return contacts, skipped


def _same_record(a, b):
    keys = ("organization", "organization_role", "full_name", "phone", "responsibility")
    return all(_norm(a[k]) == _norm(b[k]) for k in keys)


def _candidate_match(a, b):
    ap, bp = _normalize_phone(a["phone"]), _normalize_phone(b["phone"])
    if ap and bp and ap == bp:
        return True
    an, bn = _norm(a["full_name"]), _norm(b["full_name"])
    ao, bo = _norm(a["organization"]), _norm(b["organization"])
    ar, br = _norm(a["organization_role"]), _norm(b["organization_role"])
    if an and bn and an == bn and ((ao and ao == bo) or (ar and ar == br)):
        return True
    if not an and not bn and ao and ao == bo and ar == br:
        return True
    return False


def backup_database(db_path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path("/var/lib/az-baze/contact-import-backups") / f"contacts-excel-{stamp}"
    directory.mkdir(parents=True, exist_ok=False)
    destination = directory / "auth-before-import.db"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(destination)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ContactImportError("Резервная копия базы не прошла проверку целостности.")
    finally:
        check.close()
    return str(directory)


def import_contacts(conn, contacts, actor_user_id, source_name):
    existing = [dict(r) for r in conn.execute("SELECT * FROM work_contacts ORDER BY id").fetchall()]
    already_present = []
    conflicts = []
    pending = []

    for contact in contacts:
        matches = [row for row in existing if _candidate_match(contact, row)]
        if not matches:
            pending.append(contact)
        elif any(_same_record(contact, row) for row in matches):
            already_present.append(contact)
        else:
            conflicts.append({
                "contact": contact,
                "existing_ids": [row["id"] for row in matches],
            })

    if conflicts:
        ids = sorted({item for conflict in conflicts for item in conflict["existing_ids"]})
        raise ContactImportConflict(
            "Найдены похожие существующие контакты с отличающимися данными. "
            "Импорт остановлен без изменений. ID для проверки: " + ", ".join(map(str, ids))
        )

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    inserted_ids = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for contact in pending:
            cur = conn.execute(
                """
                INSERT INTO work_contacts(
                    organization,organization_role,full_name,phone,responsibility,
                    created_by,updated_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    contact["organization"],
                    contact["organization_role"],
                    contact["full_name"],
                    contact["phone"],
                    contact["responsibility"],
                    actor_user_id,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            inserted_ids.append(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
                VALUES(?, 'work_contact_imported', NULL, ?, ?)
                """,
                (
                    actor_user_id,
                    f"contact_id={cur.lastrowid}; source={source_name}; sheet=Все контакты",
                    now,
                ),
            )

        after = [dict(r) for r in conn.execute("SELECT * FROM work_contacts ORDER BY id").fetchall()]
        matched = sum(
            1 for contact in contacts
            if any(_candidate_match(contact, row) and _same_record(contact, row) for row in after)
        )
        if matched != len(contacts):
            raise ContactImportError(
                f"Проверка после импорта не прошла: найдено {matched} из {len(contacts)} контактов."
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "source_count": len(contacts),
        "inserted": len(inserted_ids),
        "already_present": len(already_present),
        "matched": len(contacts),
        "inserted_ids": inserted_ids,
    }
