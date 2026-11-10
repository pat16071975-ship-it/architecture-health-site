import os
import unittest
from unittest.mock import patch

os.environ.setdefault("AZBAZE_SECRET_KEY", "split-services-test")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-split-services-test.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-split-services-site")

import services_upload


class ServicesUploadTests(unittest.TestCase):
    def test_parse_services_does_not_require_completed_dates(self):
        parsed = (
            [
                {
                    "staff": "Чирков М. С.",
                    "patient": "Пациент А",
                    "date": "2026-01-09",
                    "group": "Терапия",
                    "service": "Лечение",
                    "qty": 1,
                    "amount": 1000,
                    "invoice": "1",
                },
                {
                    "staff": "Администраторы -.",
                    "patient": "Пациент Б",
                    "date": "2026-01-20",
                    "group": "Сопутствующие товары",
                    "service": "Паста",
                    "qty": 1,
                    "amount": 1500,
                    "invoice": "2",
                },
            ],
            [],
            (2026, 1),
        )
        with patch.object(
            services_upload.daily_upload,
            "_parse_fixed_raw",
            return_value=(parsed, "Выручка по направлениям"),
        ):
            prepared = services_upload._parse(b"xlsx", "revenue.xlsx")

        self.assertEqual(
            prepared["dates"],
            ["2026-01-09", "2026-01-20"],
        )
        first = prepared["days"]["2026-01-09"]["normalized"]
        second = prepared["days"]["2026-01-20"]["normalized"]
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["visits"], [])
        self.assertEqual(first["overall"], {"2026-01-09": {}})
        self.assertEqual(len(second["retail_items"]), 1)
        self.assertEqual(second["overall"], {"2026-01-20": {}})


    def test_current_dental_providers_are_known_without_manual_direction(self):
        self.assertIn("Морозова А. П.", services_upload.core.ident_import.DENTISTS)
        self.assertIn("Игнатенко Д. С.", services_upload.core.ident_import.DENTISTS)

    def test_two_repeat_visits_across_two_departments_auto_split_one_each(self):
        visits = [
            {"date": "2026-01-06", "patient": "Пациент А", "kind": "Повторные"},
            {"date": "2026-01-06", "patient": "Пациент А", "kind": "Повторные"},
        ]
        items = [
            {
                "staff": "Зубачев Р. Н.",
                "patient": "Пациент А",
                "date": "2026-01-06",
                "service": "Повторный осмотр-приём",
                "amount": 1000,
            },
            {
                "staff": "Старостенко В. А.",
                "patient": "Пациент А",
                "date": "2026-01-06",
                "service": "Остеопатическое лечение",
                "amount": 7000,
            },
        ]
        doctors = services_upload.daily_upload._doctor_attribution(visits, items)
        self.assertEqual(doctors["2026-01-06"]["Повторные"]["Зубачев Р. Н."], 1)
        self.assertEqual(doctors["2026-01-06"]["Повторные"]["Старостенко В. А."], 1)

    def test_one_visit_across_two_departments_stays_unassigned_without_guess(self):
        visits = [
            {"date": "2026-01-06", "patient": "Пациент А", "kind": "Повторные"},
        ]
        items = [
            {
                "staff": "Зубачев Р. Н.",
                "patient": "Пациент А",
                "date": "2026-01-06",
                "service": "Осмотр",
                "amount": 1000,
            },
            {
                "staff": "Старостенко В. А.",
                "patient": "Пациент А",
                "date": "2026-01-06",
                "service": "Лечение",
                "amount": 7000,
            },
        ]
        doctors = services_upload.daily_upload._doctor_attribution(visits, items)
        self.assertEqual(dict(doctors), {})

    def test_compare_services_treats_month_as_authoritative(self):
        existing = {
            "2026-01-05": {"has_services": True, "services_hash": "same"},
            "2026-01-06": {"has_services": True, "services_hash": "old"},
            "2026-01-07": {"has_services": True, "services_hash": "gone"},
            "2026-01-08": {"has_services": False, "services_hash": "empty"},
        }
        prepared = {
            "days": {
                "2026-01-05": {"services_hash": "same"},
                "2026-01-06": {"services_hash": "new"},
                "2026-01-08": {"services_hash": "fresh"},
            }
        }
        compared = services_upload.compare_services(existing, prepared)
        self.assertEqual(compared["identical"], ["2026-01-05"])
        self.assertEqual(compared["conflict"], ["2026-01-06"])
        self.assertEqual(compared["new"], ["2026-01-08"])
        self.assertEqual(compared["removed"], ["2026-01-07"])

    def test_empty_service_data_initializes_after_migration(self):
        data = services_upload._empty_service_data(2026)
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["months"], [])
        self.assertIn("Стоматология", data["directions"])
        self.assertIn("Отделение структуры", data["directions"])
        self.assertIn("Лаборатория", data["directions"])
        self.assertIn(
            "Лечение кариеса / пломбирование",
            data["directions"]["Стоматология"]["categories"],
        )

        idx = services_upload._ensure_months_through(data, 2026, 3)
        self.assertEqual(idx, 2)
        self.assertEqual(data["months"], ["Январь", "Февраль", "Март"])

    def test_services_merge_preserves_completed_and_rebuilds_doctor(self):
        existing = {
            "data_date": "2026-01-05",
            "overall": {"2026-01-05": {"Первичные": 1}},
            "visits": [
                {"date": "2026-01-05", "patient": "Пациент А", "kind": "Первичные"}
            ],
            "items": [],
            "retail_items": [],
            "lab_invoices": [],
            "doctors": {},
        }
        incoming = {
            "data_date": "2026-01-05",
            "items": [
                {
                    "staff": "Чирков М. С.",
                    "patient": "Пациент А",
                    "date": "2026-01-05",
                    "group": "Терапия",
                    "service": "Лечение",
                    "qty": 1,
                    "amount": 1000,
                }
            ],
            "retail_items": [],
            "lab_invoices": [],
        }
        merged = services_upload._merged_normalized(existing, incoming)
        self.assertEqual(merged["overall"], existing["overall"])
        self.assertEqual(merged["visits"], existing["visits"])
        self.assertEqual(
            merged["doctors"]["2026-01-05"]["Первичные"]["Чирков М. С."],
            1,
        )

    def test_clear_services_does_not_delete_completed_layer(self):
        existing = {
            "data_date": "2026-01-05",
            "overall": {"2026-01-05": {"Повторные": 2}},
            "visits": [
                {"date": "2026-01-05", "patient": "А", "kind": "Повторные"},
                {"date": "2026-01-05", "patient": "Б", "kind": "Повторные"},
            ],
            "items": [{"service": "old"}],
            "retail_items": [{"service": "retail"}],
            "lab_invoices": [["1", "2026-01-05"]],
            "doctors": {"2026-01-05": {"Повторные": {"Чирков М. С.": 2}}},
        }
        cleared = services_upload._clear_services(existing)
        self.assertEqual(cleared["overall"], existing["overall"])
        self.assertEqual(cleared["visits"], existing["visits"])
        self.assertEqual(cleared["items"], [])
        self.assertEqual(cleared["retail_items"], [])
        self.assertEqual(cleared["lab_invoices"], [])
        self.assertEqual(cleared["doctors"], {})


if __name__ == "__main__":
    unittest.main()
