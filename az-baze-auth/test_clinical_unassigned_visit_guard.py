from pathlib import Path
import sqlite3
import unittest

import daily_upload
import upload_reconcile


class ClinicalVisitDirectionTableTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        upload_reconcile.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def period_without_evidence(self):
        return [
            {
                "data_date": "2026-10-26",
                "normalized": {
                    "overall": {"2026-10-26": {"Повторные": 2}},
                    "visits": [
                        {"date": "2026-10-26", "patient": "Иванов И. И.", "kind": "Повторные"},
                        {"date": "2026-10-26", "patient": "Петров П. П.", "kind": "Повторные"},
                    ],
                    "items": [],
                    "doctors": {"2026-10-26": {}},
                },
            }
        ]

    def test_patient_level_rows_and_saved_choices(self):
        period = self.period_without_evidence()
        rows = daily_upload._visit_direction_rows(period, self.conn)
        self.assertEqual([row["patient"] for row in rows], ["Иванов И. И.", "Петров П. П."])
        self.assertTrue(all(row["evidence"] == [] for row in rows))

        decisions = {
            rows[0]["visit_id"]: "structure",
            rows[1]["visit_id"]: "unassigned",
        }
        unresolved, resolved = daily_upload._apply_visit_direction_rows(
            period, rows, decisions
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(resolved, decisions)
        self.assertEqual(
            period[0]["normalized"]["manual_direction_counts"]["structure"]["Повторные"],
            1,
        )

        upload_reconcile.save_visit_direction_overrides(
            self.conn, decisions, rows, actor_id=7
        )
        self.conn.commit()

        again = daily_upload._visit_direction_rows(
            self.period_without_evidence(), self.conn
        )
        self.assertEqual(
            [row["saved_direction"] for row in again],
            ["structure", "unassigned"],
        )

        columns = {
            row["name"]
            for row in self.conn.execute(
                "PRAGMA table_info(clinical_visit_direction_overrides)"
            )
        }
        self.assertNotIn("patient_name", columns)
        self.assertNotIn("patient", columns)

    def test_ambiguous_evidence_shows_staff_service_and_amount_without_guessing(self):
        patient = "Сидоров С. С."
        period = [
            {
                "data_date": "2026-10-27",
                "normalized": {
                    "overall": {"2026-10-27": {"Первичные": 1}},
                    "visits": [
                        {"date": "2026-10-27", "patient": patient, "kind": "Первичные"}
                    ],
                    "items": [
                        {
                            "date": "2026-10-27",
                            "patient": patient,
                            "staff": "Назыров У. Ж.",
                            "service": "Стоматологическая услуга",
                            "amount": 4500,
                        },
                        {
                            "date": "2026-10-27",
                            "patient": patient,
                            "staff": "Лапина Н. С.",
                            "service": "Приём специалиста",
                            "amount": 3000,
                        },
                    ],
                    "doctors": {"2026-10-27": {}},
                },
            }
        ]
        rows = daily_upload._visit_direction_rows(period, self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patient"], patient)
        self.assertEqual(
            {row["amount"] for row in rows[0]["evidence"]},
            {4500.0, 3000.0},
        )
        self.assertEqual(
            set(rows[0]["candidate_directions"]),
            {"dent", "structure"},
        )

    def test_unresolved_direction_is_optional_for_current_upload(self):
        period = self.period_without_evidence()
        rows = daily_upload._visit_direction_rows(period, self.conn)
        unresolved, resolved = daily_upload._apply_visit_direction_rows(
            period, rows, decisions={}
        )
        self.assertEqual(len(unresolved), 2)
        self.assertEqual(resolved, {})
        self.assertEqual(
            period[0]["normalized"]["manual_direction_counts"],
            {"dent": {}, "structure": {}},
        )
        source = (
            Path(__file__).resolve().parent / "daily_upload.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "Для каждой строки выберите «Стоматология», «Клиника»",
            source,
        )

    def test_upload_ui_renders_patient_visit_direction_table(self):
        html = (
            Path(__file__).resolve().parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        for text in (
            "Пациент",
            "Тип приёма",
            "Что нашли в источниках",
            "Куда отнести",
            "Стоматология",
            "Клиника",
            "Оставить нераспределённым",
            "data-visit-direction",
            "visit_direction_decisions",
            "Можно продолжить загрузку без выбора",
            "continueBtn.disabled=unknown.length>0&&!payload.can_replace",
            "if(select.value)result[select.dataset.visitDirection]=select.value",
            "const needs=out.requires_choice||out.requires_provider_mapping;",
            'id="reconcileCloseText"',
            ">Закрыть</button>",
            ".reconcile-overlay{padding:0;place-items:stretch}",
            "height:100dvh;max-height:100dvh",
            ".reconcile-head{position:sticky;top:0",
            ".reconcile-foot{position:sticky;bottom:0",
            "closeTextBtn?.addEventListener('click',closePreview)",
        ):
            self.assertIn(text, html)
        self.assertNotIn(
            "out.requires_choice||out.requires_provider_mapping||out.requires_review",
            html,
        )


if __name__ == "__main__":
    unittest.main()
