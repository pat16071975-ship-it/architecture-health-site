import unittest
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


if __name__ == "__main__":
    unittest.main()
