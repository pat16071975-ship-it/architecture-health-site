import re
import unittest
from datetime import datetime
from types import SimpleNamespace

import upload_integrity


class FakeConn:
    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return []


class UploadIntegrityTests(unittest.TestCase):
    def setUp(self):
        ident = SimpleNamespace(
            DENTISTS={"Dent D.": "Dent Doctor"},
            STRUCTURE_DOCTORS={"Struct S.": "Struct Doctor"},
        )
        self.core = SimpleNamespace(ident_import=ident)

    def test_unassigned_repeat_does_not_inflate_management_repeat(self):
        normalized = {
            "data_date": "2026-09-14",
            "items": [],
            "lab_invoices": [],
            "overall": {"2026-09-14": {"Повторные": 6}},
            "doctors": {"2026-09-14": {}},
        }
        delta = upload_integrity.daily_delta(self.core, normalized)
        self.assertEqual(delta["repeat"], 0)
        self.assertEqual(delta["sourceRepeat"], 6)
        self.assertEqual(delta["unassignedRepeat"], 6)

    def test_attributed_visits_define_primary_and_repeat(self):
        normalized = {
            "data_date": "2026-09-14",
            "items": [],
            "lab_invoices": [],
            "overall": {
                "2026-09-14": {
                    "Первичные": 4,
                    "Повторные": 7,
                }
            },
            "doctors": {
                "2026-09-14": {
                    "Первичные": {"Dent D.": 1, "Struct S.": 2},
                    "Повторные": {"Dent D.": 2, "Struct S.": 3},
                }
            },
        }
        delta = upload_integrity.daily_delta(self.core, normalized)
        self.assertEqual(delta["primary"], 3)
        self.assertEqual(delta["repeat"], 5)
        self.assertEqual(delta["unassignedPrimary"], 1)
        self.assertEqual(delta["unassignedRepeat"], 2)

    def test_discount_data_complete_when_source_columns_reconcile(self):
        normalized = {
            "data_date": "2026-10-01",
            "items": [
                {
                    "staff": "Dent D.",
                    "amount": 80,
                    "gross_amount": 100,
                    "discount_amount": 20,
                },
                {
                    "staff": "Struct S.",
                    "amount": 50,
                    "gross_amount": 50,
                    "discount_amount": 0,
                },
            ],
            "lab_invoices": [],
            "overall": {"2026-10-01": {}},
            "doctors": {"2026-10-01": {}},
        }
        delta = upload_integrity.daily_delta(self.core, normalized)
        self.assertEqual(delta["grossRevenue"], 150)
        self.assertEqual(delta["discountAmount"], 20)
        self.assertTrue(delta["discountDataComplete"])
        self.assertEqual(delta["factMedicine"], 130)

    def test_missing_discount_columns_stay_incomplete(self):
        normalized = {
            "data_date": "2026-09-14",
            "items": [{"staff": "Dent D.", "amount": 80}],
            "lab_invoices": [],
            "overall": {"2026-09-14": {}},
            "doctors": {"2026-09-14": {}},
        }
        delta = upload_integrity.daily_delta(self.core, normalized)
        self.assertFalse(delta["discountDataComplete"])
        self.assertEqual(delta["grossRevenue"], 0)
        self.assertEqual(delta["discountAmount"], 0)
        self.assertEqual(delta["factMedicine"], 80)

    def test_enrich_service_items_reads_price_discount_and_net(self):
        ident = SimpleNamespace(
            KNOWN_STAFF={"Dent D."},
            INVOICE_RE=re.compile(r"Счет №(\d+) от (\d{2}\.\d{2}\.\d{4})"),
            _iso=lambda value: datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d"),
            _money=lambda value: float(str(value).replace(" ", "").replace(",", ".")) if str(value).strip() else None,
        )
        core = SimpleNamespace(ident_import=ident)
        parsed = (
            [
                {
                    "staff": "Dent D.",
                    "date": "2026-09-14",
                    "group": "Лечение",
                    "service": "Услуга",
                    "qty": 1.0,
                    "amount": 80.0,
                    "invoice": "123",
                }
            ],
            [],
            (2026, 9),
        )
        text = (
            "header\nheader2\nDent D.\n"
            "Счет №123 от 14.09.2026 10:00:00\n"
            "Лечение\t14.09.2026 10:00\tУслуга\t1\t100\t20\t80\n"
        )
        enriched, _, _ = upload_integrity.enrich_service_items(core, parsed, text)
        self.assertEqual(enriched[0]["gross_amount"], 100)
        self.assertEqual(enriched[0]["discount_amount"], 20)

    def test_full_import_management_gets_cumulative_discount_fields(self):
        management = {"2026-10-01": {}, "2026-10-02": {}}
        items = [
            {"date": "2026-10-01", "amount": 80, "gross_amount": 100, "discount_amount": 20},
            {"date": "2026-10-02", "amount": 50, "gross_amount": 50, "discount_amount": 0},
        ]
        result = upload_integrity.enrich_management_discounts(management, items)
        self.assertEqual(result["2026-10-01"]["grossRevenue"], 100)
        self.assertEqual(result["2026-10-01"]["discountAmount"], 20)
        self.assertTrue(result["2026-10-01"]["discountDataComplete"])
        self.assertEqual(result["2026-10-02"]["grossRevenue"], 150)
        self.assertEqual(result["2026-10-02"]["discountAmount"], 20)
        self.assertTrue(result["2026-10-02"]["discountDataComplete"])

    def test_merge_preserves_unmanaged_payload_and_marketing_sources(self):
        existing = {
            "date": "2026-09-13",
            "plan": 123,
            "manualFlag": "keep",
            "marketingSources": {"2ГИС": 3},
        }
        managed = {"date": "2026-09-13", "plan": 456, "repeat": 205}
        record = upload_integrity.merge_payload(existing, managed)
        self.assertEqual(record["plan"], 456)
        self.assertEqual(record["repeat"], 205)
        self.assertEqual(record["manualFlag"], "keep")
        self.assertEqual(record["marketingSources"], {"2ГИС": 3})
        self.assertEqual(record["marketingSourcesAsOf"], "2026-09-13")

    def test_merge_carries_latest_marketing_snapshot_to_new_day(self):
        record = upload_integrity.merge_payload(
            {},
            {"date": "2026-09-14", "repeat": 205},
            marketing_sources={"2ГИС": 3, "ПроДокторов": 9},
            marketing_as_of="2026-09-13",
        )
        self.assertEqual(record["marketingSources"]["ПроДокторов"], 9)
        self.assertEqual(record["marketingSourcesAsOf"], "2026-09-13")

    def test_rebuild_keeps_repeat_at_department_sum_and_records_gap(self):
        normalized = {
            "data_date": "2026-09-14",
            "items": [],
            "lab_invoices": [],
            "overall": {"2026-09-14": {"Повторные": 6}},
            "doctors": {"2026-09-14": {}},
        }
        baseline = {
            "date": "2026-09-13",
            "primary": 28,
            "repeat": 205,
            "dentPrimary": 10,
            "dentRepeat": 144,
            "clinicPrimary": 18,
            "clinicRepeat": 61,
            "factMedicine": 4756930,
            "factLab": 238750,
            "dentists": {"Dent Doctor": 0},
            "clinicDocs": {"Struct Doctor": 0},
            "labOrders": 6,
            "labRevenue": 238750,
        }
        existing = {"date": "2026-09-14", "manualFlag": "keep"}
        core = SimpleNamespace(
            ident_import=self.core.ident_import,
            _active_month_rows=lambda _month: [("2026-09-14", normalized)],
            _latest_prior_report=lambda _date: baseline,
            _load_report=lambda _date: existing,
            db=lambda: FakeConn(),
        )
        rebuilt = upload_integrity.rebuild_management(core, "2026-09", "test")
        record = rebuilt["2026-09-14"]
        self.assertEqual(record["repeat"], 205)
        self.assertEqual(record["dentRepeat"] + record["clinicRepeat"], 205)
        self.assertEqual(record["_uploadControl"]["sourceRepeat"], 211)
        self.assertEqual(record["_uploadControl"]["unassignedRepeat"], 6)
        self.assertEqual(record["manualFlag"], "keep")
        self.assertFalse(record["discountDataComplete"])

    def test_fresh_month_can_establish_complete_discount_data(self):
        normalized = {
            "data_date": "2026-10-01",
            "items": [
                {
                    "staff": "Dent D.",
                    "amount": 80,
                    "gross_amount": 100,
                    "discount_amount": 20,
                }
            ],
            "lab_invoices": [],
            "overall": {"2026-10-01": {}},
            "doctors": {"2026-10-01": {}},
        }
        core = SimpleNamespace(
            ident_import=self.core.ident_import,
            _active_month_rows=lambda _month: [("2026-10-01", normalized)],
            _latest_prior_report=lambda _date: {},
            _load_report=lambda _date: {},
            db=lambda: FakeConn(),
        )
        rebuilt = upload_integrity.rebuild_management(core, "2026-10", "test")
        record = rebuilt["2026-10-01"]
        self.assertTrue(record["discountDataComplete"])
        self.assertEqual(record["grossRevenue"], 100)
        self.assertEqual(record["discountAmount"], 20)


if __name__ == "__main__":
    unittest.main()
