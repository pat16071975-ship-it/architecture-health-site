from pathlib import Path
import unittest

import daily_upload


class ClinicalUnassignedVisitGuardTests(unittest.TestCase):
    def test_only_unassigned_direction_visits_require_review(self):
        period = [
            {
                "data_date": "2026-10-25",
                "normalized": {
                    "overall": {
                        "2026-10-25": {
                            "Повторные": 7,
                            "Отконсультированные": 1,
                        }
                    },
                    "doctors": {
                        "2026-10-25": {
                            "Повторные": {"Dent D.": 6, "Struct S.": 1},
                            "Отконсультированные": {"Struct S.": 1},
                        }
                    },
                },
            },
            {
                "data_date": "2026-10-26",
                "normalized": {
                    "overall": {"2026-10-26": {"Повторные": 2}},
                    "doctors": {"2026-10-26": {}},
                },
            },
        ]

        self.assertEqual(
            daily_upload._unassigned_visit_summary(period),
            [
                {
                    "date": "2026-10-26",
                    "primary": 0,
                    "repeat": 2,
                    "total": 2,
                }
            ],
        )

    def test_primary_and_repeat_are_reported_separately(self):
        period = [
            {
                "data_date": "2026-10-27",
                "normalized": {
                    "overall": {
                        "2026-10-27": {
                            "Первичные": 3,
                            "Повторные": 4,
                            "Отконсультированные": 1,
                        }
                    },
                    "doctors": {
                        "2026-10-27": {
                            "Первичные": {"Dent D.": 2},
                            "Повторные": {"Struct S.": 3},
                        }
                    },
                },
            }
        ]

        self.assertEqual(
            daily_upload._unassigned_visit_summary(period),
            [
                {
                    "date": "2026-10-27",
                    "primary": 1,
                    "repeat": 2,
                    "total": 3,
                }
            ],
        )

    def test_upload_ui_explains_that_unknown_direction_is_not_guessed(self):
        html = (
            Path(__file__).resolve().parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Есть приёмы, для которых направление определить нельзя", html)
        self.assertIn("не будут самовольно отнесены к стоматологии или клинике", html)
        self.assertIn("payload.unassigned_visits||[]", html)


if __name__ == "__main__":
    unittest.main()
