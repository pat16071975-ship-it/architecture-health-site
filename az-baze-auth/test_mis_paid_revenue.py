import re
import unittest
from types import SimpleNamespace

import mis_paid_revenue
import upload_integrity
import upload_reconcile


INVOICE_RE = re.compile(r"^Счет №(\d+) от (\d{2}\.\d{2}\.\d{4})")


def iso_date(value):
    day, month, year = value.split(".")
    return f"{year}-{month}-{day}"


def money(value):
    text = str(value or "").replace("₽", "").replace("\xa0", " ").strip()
    text = text.replace(" ", "").replace(",", ".")
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


KNOWN = {
    "Иванова А. В.",
    "Чирков М. С.",
    "Казанцев Л. Е.",
    "Старшийадминистратор -.",
}


def is_staff_header(value):
    return str(value or "").strip() in KNOWN or bool(
        re.fullmatch(
            r"^(?:[A-Za-zА-ЯЁа-яё-]+(?:\s+[A-Za-zА-ЯЁа-яё-]+)*)\s+[A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.)?$",
            str(value or "").strip(),
        )
    )


def expanded_text():
    rows = [
        ["Группа услуг", "Услуги", "", "", " Задолженность на нач. периода ", "Сумма со скидкой", " Оплачено ", " Задолженность на конец периода "],
        ["Итого", "", "", "", "100", "1000", "900", "200"],
        ["Авансы", "", "", "", "-50", "-", "50", "-100"],
        ["Услуги", "", "", "", "150", "1000", "850", "300"],
        ["Иванова А. В.", "", "", "", "0", "300", "225", "75"],
        ["Пациент А", "", "", "", "0", "300", "225", "75"],
        ["Счет №10 от 16.09.2026 10:00:00", "", "", "", "-", "300", "225", "75"],
        ["Ортодонтия", "16.09.2026 10:00", "Фиксация брекет системы", "1", "-", "300", "225", "75"],
        ["Чирков М. С.", "", "", "", "50", "600", "600", "50"],
        ["Пациент Б", "", "", "", "50", "600", "600", "50"],
        ["Счет №11 от 20.09.2026 11:00:00", "", "", "", "-", "600", "600", "0"],
        ["Терапия", "20.09.2026 11:00", "Лечение", "1", "-", "600", "600", "0"],
        ["Крылова А. А.", "", "", "", "100", "0", "25", "75"],
        ["Неоплаченные услуги на начало периода", "", "", "", "100", "-", "25", "75"],
    ]
    return "\n".join("\t".join(map(str, row)) for row in rows)


class MisPaidRevenueTests(unittest.TestCase):
    def test_expanded_report_parses_service_rows_and_provider_paid_snapshot(self):
        parsed = mis_paid_revenue.parse_expanded_report(
            expanded_text(),
            is_staff_header=is_staff_header,
            invoice_re=INVOICE_RE,
            iso_date=iso_date,
            money=money,
            lab_doctors={"Казанцев Л. Е.": "Казанцев Л. Е."},
        )
        items, lab_invoices, month, snapshot = parsed

        self.assertEqual(month, (2026, 9))
        self.assertEqual(lab_invoices, [])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["staff"], "Иванова А. В.")
        self.assertEqual(items[0]["date"], "2026-09-16")
        self.assertEqual(items[0]["amount"], 300)
        self.assertEqual(items[0]["paid_amount"], 225)

        self.assertEqual(snapshot["totals"]["paid"], 850)
        self.assertEqual(snapshot["providers"]["Иванова А. В."]["paid"], 225)
        self.assertEqual(snapshot["providers"]["Чирков М. С."]["paid"], 600)
        self.assertEqual(snapshot["providers"]["Крылова А. А."]["paid"], 25)
        self.assertEqual(snapshot["provider_paid_total"], 850)
        self.assertEqual(snapshot["provider_paid_delta"], 0)

    def test_expanded_report_rejects_broken_service_balance(self):
        broken = expanded_text().replace(
            "Услуги\t\t\t\t150\t1000\t850\t300",
            "Услуги\t\t\t\t150\t1000\t850\t301",
        )
        with self.assertRaisesRegex(ValueError, "не сходится баланс"):
            mis_paid_revenue.parse_expanded_report(
                broken,
                is_staff_header=is_staff_header,
                invoice_re=INVOICE_RE,
                iso_date=iso_date,
                money=money,
                lab_doctors={},
            )

    def test_paid_snapshot_maps_provider_paid_by_registry_without_cash_legal_split(self):
        core = SimpleNamespace(
            ident_import=SimpleNamespace(
                DENTISTS={"Иванова А. В.": "Иванова А. В."},
                STRUCTURE_DOCTORS={"Чирков М. С.": "Чирков М. С."},
                LAB_DOCTORS={},
            )
        )
        snapshot = {
            "rule": "mis-provider-paid-period-snapshot-v1",
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "totals": {
                "debt_start": 150,
                "billed_net": 1000,
                "paid": 850,
                "debt_end": 300,
            },
            "providers": {
                "Иванова А. В.": {"debt_start": 0, "billed_net": 300, "paid": 225, "debt_end": 75},
                "Чирков М. С.": {"debt_start": 50, "billed_net": 600, "paid": 600, "debt_end": 50},
                "Крылова А. А.": {"debt_start": 100, "billed_net": 0, "paid": 25, "debt_end": 75},
            },
        }
        fields = upload_integrity._paid_snapshot_fields(core, snapshot)
        self.assertEqual(fields["paidDentists"], {"Иванова А. В.": 225.0})
        self.assertEqual(fields["paidClinicDocs"], {"Чирков М. С.": 600.0})
        self.assertEqual(fields["paidKnownTotal"], 825)
        self.assertEqual(fields["paidUnclassifiedTotal"], 25)
        self.assertNotIn("dentistsLegal", fields)
        self.assertNotIn("clinicDocsLegal", fields)

    def test_snapshot_only_provider_mapping_ignores_dormant_debt_but_flags_paid(self):
        ident = SimpleNamespace(
            DENTISTS={"Иванова А. В.": "Иванова А. В."},
            STRUCTURE_DOCTORS={},
            LAB_DOCTORS={},
            KNOWN_STAFF={"Иванова А. В."},
        )
        period = [{
            "normalized": {
                "items": [],
                "doctors": {},
                "paid_snapshot": {
                    "providers": {
                        "Старый В. В.": {"debt_start": 100, "paid": 0},
                        "Крылова А. А.": {"debt_start": 100, "paid": 25},
                    }
                },
            }
        }]
        unknown = upload_reconcile.detect_unknown_providers(period, ident)
        self.assertEqual(unknown, ["Крылова А. А."])


if __name__ == "__main__":
    unittest.main()
