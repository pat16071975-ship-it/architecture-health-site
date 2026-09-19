import io
import sqlite3
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contact_excel_import as ci


def _xlsx_bytes(rows):
    def cell(ref, value):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

    sheet_rows = []
    for rnum, row in enumerate(rows, start=1):
        cells = []
        for cnum, value in enumerate(row):
            col = chr(ord("A") + cnum)
            cells.append(cell(f"{col}{rnum}", str(value)))
        sheet_rows.append(f'<row r="{rnum}">{"".join(cells)}</row>')

    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Все контакты" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    sheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


class ContactExcelParseTests(unittest.TestCase):
    def test_extracts_contacts_and_skips_heading_only_rows(self):
        data = _xlsx_bytes([
            ci.EXPECTED_HEADERS,
            ["1", "IT", "Поддержка", "Иванов Иван - руководитель", "89991234567"],
            ["2", "", "Настройка", "Петров Петр - системный администратор", "+7 999 222-33-44"],
            ["3", "ZOOM", "", "", ""],
            ["4", "Юрист (трудовое право)", "Консультации", "Сидоров Сергей", "89995556677"],
        ])
        contacts, skipped = ci.extract_contacts(data)
        self.assertEqual(len(contacts), 3)
        self.assertEqual(skipped, 1)
        self.assertEqual(contacts[0]["organization"], "IT")
        self.assertEqual(contacts[0]["organization_role"], "Руководитель")
        self.assertEqual(contacts[0]["phone"], "+7 999 123-45-67")
        self.assertEqual(contacts[1]["organization"], "IT")
        self.assertEqual(contacts[1]["organization_role"], "Системный администратор")
        self.assertEqual(contacts[2]["organization"], "")
        self.assertEqual(contacts[2]["organization_role"], "Юрист (трудовое право)")

    def test_rejects_wrong_headers(self):
        data = _xlsx_bytes([["A", "B", "C", "D", "E"], ["1", "Org", "Resp", "Name", "Phone"]])
        with self.assertRaises(ci.ContactImportError):
            ci.extract_contacts(data)


class ContactImportDatabaseTests(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE work_contacts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization TEXT NOT NULL DEFAULT '',
                organization_role TEXT NOT NULL DEFAULT '',
                full_name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                responsibility TEXT NOT NULL DEFAULT '',
                created_by INTEGER,
                updated_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                target_user_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );
        """)
        return conn

    def test_skips_identical_and_inserts_missing(self):
        conn = self._conn()
        existing = {
            "organization": "IT",
            "organization_role": "Руководитель",
            "full_name": "Иванов Иван",
            "phone": "+7 999 123-45-67",
            "responsibility": "Поддержка",
        }
        conn.execute("""
            INSERT INTO work_contacts(
                organization,organization_role,full_name,phone,responsibility,
                created_by,updated_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,1,1,'now','now')
        """, tuple(existing[k] for k in ("organization","organization_role","full_name","phone","responsibility")))
        conn.commit()

        missing = {
            "organization": "Пилигрим",
            "organization_role": "Управляющий",
            "full_name": "Петров Петр",
            "phone": "+7 999 222-33-44",
            "responsibility": "Управляющий",
        }
        result = ci.import_contacts(conn, [existing, missing], 1, "test.xlsx")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["already_present"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM work_contacts").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 1)

    def test_conflict_stops_without_writes(self):
        conn = self._conn()
        conn.execute("""
            INSERT INTO work_contacts(
                organization,organization_role,full_name,phone,responsibility,
                created_by,updated_by,created_at,updated_at
            ) VALUES('IT','Руководитель','Иванов Иван','+7 999 123-45-67','Старое',1,1,'now','now')
        """)
        conn.commit()
        incoming = {
            "organization": "IT",
            "organization_role": "Руководитель",
            "full_name": "Иванов Иван",
            "phone": "+7 999 123-45-67",
            "responsibility": "Новое",
        }
        with self.assertRaises(ci.ContactImportConflict):
            ci.import_contacts(conn, [incoming], 1, "test.xlsx")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM work_contacts").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
