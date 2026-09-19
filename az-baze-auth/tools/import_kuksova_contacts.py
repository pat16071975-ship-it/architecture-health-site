#!/usr/bin/env python3
import argparse
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

APP_ROOT = Path("/opt/az-baze-auth")
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_HEADERS = ["#", "Наименование", "По каким вопросам обращаться", "ФИО", "Номер телефона"]
EXPECTED_CONTACTS = 15


def clean(value):
    if value is None:
        return ""
    value = str(value).replace("\t", " ").replace('""', '"').strip()
    value = re.sub(r"[ \u00a0]+", " ", value)
    value = re.sub(r"\n\s*·\s*", "\n• ", value)
    value = value.replace('.)"', ".)").rstrip('"').strip()
    return value


def norm(value):
    return re.sub(r"\s+", " ", clean(value)).casefold()


def normalize_phone(value):
    raw = clean(value)
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw


def split_name_role(value):
    value = clean(value)
    match = re.match(r"^(.*?)\s*[-–—]\s*(.+)$", value)
    if not match:
        return value, ""
    name = match.group(1).strip()
    role = match.group(2).strip()
    if role:
        role = role[0].upper() + role[1:]
    return name, role


def column_index(cell_ref):
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - 64)
    return result - 1


def read_sheet_rows(path, sheet_name):
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{ns_main}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns_main}}}t")))

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rid = None
        for sheet in wb_root.find(f"{{{ns_main}}}sheets"):
            if sheet.attrib.get("name") == sheet_name:
                rid = sheet.attrib.get(f"{{{ns_rel}}}id")
                break
        if not rid:
            raise RuntimeError(f"Sheet not found: {sheet_name}")

        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rel_root.findall(f"{{{ns_pkg}}}Relationship"):
            if rel.attrib.get("Id") == rid:
                target = rel.attrib.get("Target")
                break
        if not target:
            raise RuntimeError("Worksheet relationship not found")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target

        ws_root = ET.fromstring(zf.read(target))
        result = []
        sheet_data = ws_root.find(f"{{{ns_main}}}sheetData")
        for row in sheet_data.findall(f"{{{ns_main}}}row"):
            values = {}
            max_col = -1
            for cell in row.findall(f"{{{ns_main}}}c"):
                idx = column_index(cell.attrib.get("r", "A1"))
                max_col = max(max_col, idx)
                typ = cell.attrib.get("t")
                value = ""
                if typ == "inlineStr":
                    is_el = cell.find(f"{{{ns_main}}}is")
                    if is_el is not None:
                        value = "".join(t.text or "" for t in is_el.iter(f"{{{ns_main}}}t"))
                else:
                    v = cell.find(f"{{{ns_main}}}v")
                    if v is not None and v.text is not None:
                        value = shared[int(v.text)] if typ == "s" else v.text
                values[idx] = value
            if max_col >= 0:
                result.append([values.get(i, "") for i in range(max_col + 1)])
        return result


def map_contact(label, responsibility, full_name, phone, current_group):
    label = clean(label)
    responsibility = clean(responsibility)
    full_name, embedded_role = split_name_role(full_name)
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
                role = "Управляющий"
        elif "юрист (трудовое право)" in low:
            role, new_group = "Юрист (трудовое право)", ""
        elif "юрист (медицинский)" in low:
            role, new_group = "Юрист (медицинский)", ""
        elif low == "патент":
            role, new_group = "Патент", ""
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
        elif "пожар" in low or "охрана труда" in low:
            role, new_group = "Пожарная безопасность / охрана труда", ""
        else:
            organization, new_group = label, label
    elif current_group:
        organization = current_group
        lowr = responsibility.casefold()
        if current_group == "Пилигрим":
            if lowr.startswith("юрист собственников"):
                role = "Юрист собственников Адамова и Павлова"
            elif lowr.startswith("администратор на ресепшен"):
                role = "Администратор ресепшен 1 этаж"

    return {
        "organization": organization,
        "organization_role": role,
        "full_name": full_name,
        "phone": normalize_phone(phone),
        "responsibility": responsibility,
    }, new_group


def extract_contacts(path):
    rows = read_sheet_rows(path, "Все контакты")
    if not rows:
        raise RuntimeError("Лист «Все контакты» пуст.")
    headers = [clean(v) for v in rows[0][:5]]
    if headers != EXPECTED_HEADERS:
        raise RuntimeError(f"Неожиданные заголовки: {headers!r}")

    contacts = []
    current_group = ""
    for row in rows[1:]:
        row = list(row) + [""] * (5 - len(row))
        _number, label, responsibility, full_name, phone = row[:5]
        if not any(clean(v) for v in (label, responsibility, full_name, phone)):
            continue
        if clean(label).upper() == "ZOOM" and not any(clean(v) for v in (responsibility, full_name, phone)):
            continue
        contact, current_group = map_contact(label, responsibility, full_name, phone, current_group)
        contacts.append(contact)

    if len(contacts) != EXPECTED_CONTACTS:
        raise RuntimeError(
            f"Ожидалось {EXPECTED_CONTACTS} контактов, найдено {len(contacts)}. Импорт остановлен."
        )
    return contacts


def same_record(a, b):
    keys = ("organization", "organization_role", "full_name", "phone", "responsibility")
    return all(norm(a[k]) == norm(b[k]) for k in keys)


def candidate_match(a, b):
    ap, bp = normalize_phone(a["phone"]), normalize_phone(b["phone"])
    if ap and bp and ap == bp:
        return True
    an, bn = norm(a["full_name"]), norm(b["full_name"])
    ao, bo = norm(a["organization"]), norm(b["organization"])
    ar, br = norm(a["organization_role"]), norm(b["organization_role"])
    if an and bn and an == bn and ((ao and ao == bo) or (ar and ar == br)):
        return True
    if not an and not bn and ao and ao == bo and ar == br:
        return True
    return False


def backup_database(db_path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path("/var/lib/az-baze/backups") / f"contacts-kuksova-{stamp}"
    directory.mkdir(parents=True, exist_ok=False)
    destination = directory / "auth-before-import.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(destination)
    src.backup(dst)
    dst.close()
    src.close()
    check = sqlite3.connect(destination)
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
    check.close()
    if result != "ok":
        raise RuntimeError("Backup integrity_check failed")
    return directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = Path(args.xlsx)
    if not source.is_file():
        raise SystemExit(f"FILE NOT FOUND: {source}")

    contacts = extract_contacts(source)
    print(f"SOURCE CONTACTS={len(contacts)}")
    print("SOURCE SHEET=Все контакты")
    print("SKIPPED=ZOOM(empty)")

    if not args.apply:
        print("PREVIEW: PASS")
        return

    import app as app_core
    import server
    import access_contacts_ext

    app = server.app
    access_contacts_ext._init_contacts_schema()
    backup_dir = backup_database(app_core.DB_PATH)
    print(f"BACKUP VERIFIED: {backup_dir}")

    with app.app_context():
        conn = app_core.db()
        existing_rows = [dict(r) for r in conn.execute("SELECT * FROM work_contacts ORDER BY id").fetchall()]
        identical = []
        conflicts = []
        pending = []

        for contact in contacts:
            matches = [row for row in existing_rows if candidate_match(contact, row)]
            if not matches:
                pending.append(contact)
                continue
            if any(same_record(contact, row) for row in matches):
                identical.append(contact)
            else:
                conflicts.append([row["id"] for row in matches])

        print(f"EXISTING CONTACTS={len(existing_rows)}")
        print(f"ALREADY PRESENT={len(identical)}")
        print(f"TO INSERT={len(pending)}")
        if conflicts:
            print(f"CONFLICTS={len(conflicts)} IDS={conflicts}")
            raise SystemExit("IMPORT REFUSED: conflicting existing contacts found; database was not changed.")

        now = app_core.iso_now()
        inserted_ids = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for contact in pending:
                cur = conn.execute(
                    """
                    INSERT INTO work_contacts(
                        organization,organization_role,full_name,phone,responsibility,
                        created_by,updated_by,created_at,updated_at
                    ) VALUES(?,?,?,?,?,NULL,NULL,?,?)
                    """,
                    (
                        contact["organization"],
                        contact["organization_role"],
                        contact["full_name"],
                        contact["phone"],
                        contact["responsibility"],
                        now,
                        now,
                    ),
                )
                inserted_ids.append(cur.lastrowid)
                conn.execute(
                    """
                    INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
                    VALUES(NULL,'work_contact_imported',NULL,?,?)
                    """,
                    (
                        f"contact_id={cur.lastrowid}; source=Передача задач Куксова Виктория.xlsx; sheet=Все контакты",
                        now,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        after_rows = [dict(r) for r in conn.execute("SELECT * FROM work_contacts ORDER BY id").fetchall()]
        imported_or_existing = 0
        for contact in contacts:
            if any(candidate_match(contact, row) and same_record(contact, row) for row in after_rows):
                imported_or_existing += 1

        if imported_or_existing != EXPECTED_CONTACTS:
            raise SystemExit(
                f"POSTCHECK FAIL: expected {EXPECTED_CONTACTS}, matched {imported_or_existing}"
            )
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("POSTCHECK FAIL: database integrity")

        print(f"INSERTED={len(inserted_ids)}")
        print(f"POSTCHECK MATCHED={imported_or_existing}/{EXPECTED_CONTACTS}")
        print("DATABASE INTEGRITY=ok")
        print("CONTACT IMPORT: PASS")


if __name__ == "__main__":
    main()
