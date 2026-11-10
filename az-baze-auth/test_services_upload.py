import os
import unittest

os.environ.setdefault("AZBAZE_SECRET_KEY", "split-services-test")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-split-services-test.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-split-services-site")

import services_upload


class ServicesUploadTests(unittest.TestCase):
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
