import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import nn_normalize


class NNNormalizeTests(unittest.TestCase):
    def test_patient_resolver_merges_duplicate_cards_by_phone_and_name_dob(self):
        master = [
            {"name": "Иванов Иван", "dob": "1980-01-01", "iin": "", "chart": "100", "phone": "7771112233", "note": "", "source_amount": 0},
            {"name": "Иванов Иван", "dob": "1980-01-01", "iin": "", "chart": "200", "phone": "7771112233", "note": "", "source_amount": 0},
        ]
        resolver = nn_normalize.PatientResolver(master)
        self.assertEqual(len(resolver.patients), 1)
        patient = next(iter(resolver.patients.values()))
        self.assertEqual(patient["charts"], ["100", "200"])

    def test_core_normalization_counts_people_not_repeat_visits_and_month_totals(self):
        master = [
            {"name": "Пациент Один", "dob": "1980-01-01", "iin": "", "chart": "1", "phone": "7000000001", "note": "ЭВЛК + МФ рекомендовано", "source_amount": 0},
            {"name": "Пациент Два", "dob": "1981-01-01", "iin": "", "chart": "2", "phone": "7000000002", "note": "", "source_amount": 0},
        ]
        registry = [
            {"date": "2026-06-01", "start": "09:00", "end": "09:30", "doctor": "Врач А", "patient": "Пациент Один", "chart": "1", "iin": "", "phone": "7000000001", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": ""},
            {"date": "2026-06-10", "start": "09:00", "end": "09:30", "doctor": "Врач А", "patient": "Пациент Один", "chart": "1", "iin": "", "phone": "7000000001", "repeat_field": "", "note": "повторный контроль", "visit_type": "Амбулаторно", "help_type": ""},
            {"date": "2026-06-20", "start": "09:00", "end": "09:30", "doctor": "Врач А", "patient": "Пациент Один", "chart": "1", "iin": "", "phone": "7000000001", "repeat_field": "", "note": "контроль после операции", "visit_type": "Амбулаторно", "help_type": ""},
            {"date": "2026-06-02", "start": "10:00", "end": "10:30", "doctor": "Врач Б", "patient": "Пациент Два", "chart": "2", "iin": "", "phone": "7000000002", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": ""},
        ]
        medical = [
            {"date": "2026-06-05", "doctor": "Врач А", "patient": "Пациент Один", "dob": "1980-01-01", "chart": "1", "complaint": "", "objective": "", "direction": "", "assignment": "", "recommendation": "", "treatment": "Протокол манипуляции: Эндоваскулярная лазерная коагуляция + минифлебэктомия"},
        ]
        services = [
            {"patient": "Пациент Один", "chart": "1", "phone": "7000000001", "date": "2026-06-01", "doctor": "Врач А", "group": "", "service": "Прием хирурга флеболога с УЗИ", "qty": 1, "amount": 4900, "comment": ""},
            {"patient": "Пациент Один", "chart": "1", "phone": "7000000001", "date": "2026-06-05", "doctor": "Врач А", "group": "", "service": "Лазерное лечение варикоза категория B", "qty": 1, "amount": 300000, "comment": ""},
            {"patient": "Пациент Два", "chart": "2", "phone": "7000000002", "date": "2026-06-02", "doctor": "Врач Б", "group": "", "service": "Прием хирурга флеболога с УЗИ", "qty": 1, "amount": 0, "comment": ""},
        ]
        payload = nn_normalize.normalize_rows(master, registry, medical, services, [], 0)

        self.assertEqual(payload["key_metrics"]["primary_total"], 2)
        self.assertEqual(payload["key_metrics"]["primary_paid"], 1)
        self.assertEqual(payload["key_metrics"]["primary_zero"], 1)
        self.assertEqual(payload["key_metrics"]["repeat_people"], 1)
        self.assertEqual(len(payload["repeats"]), 2)
        self.assertEqual(payload["key_metrics"]["treatment_people"], 1)
        self.assertEqual(payload["key_metrics"]["treatment_paid"], 1)
        self.assertEqual(payload["monthly"][0]["turnover"], 304900)
        self.assertTrue(payload["primaries"][0]["indications"]["evlk"])
        self.assertTrue(payload["primaries"][0]["indications"]["mini"])

    def test_prior_treatment_prevents_later_visit_from_becoming_primary(self):
        master = [{"name": "Пациент", "dob": "1980-01-01", "iin": "", "chart": "1", "phone": "", "note": "", "source_amount": 0}]
        registry = [{"date": "2026-06-10", "start": "09:00", "end": "09:30", "doctor": "Врач", "patient": "Пациент", "chart": "1", "iin": "", "phone": "", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": ""}]
        services = [{"patient": "Пациент", "chart": "1", "phone": "", "date": "2026-06-05", "doctor": "Врач", "group": "", "service": "Лазерное лечение варикоза", "qty": 1, "amount": 100000, "comment": ""}]
        payload = nn_normalize.normalize_rows(master, registry, [], services, [], 0)
        self.assertEqual(payload["key_metrics"]["primary_total"], 0)

    def test_deleted_visit_matches_by_name_even_when_registry_has_phone(self):
        master = [{"name": "Пациент Один", "dob": "1980-01-01", "iin": "", "chart": "1", "phone": "7000000001", "note": "", "source_amount": 0}]
        registry = [{"date": "2026-06-01", "start": "09:00", "end": "09:30", "doctor": "Врач А Полный", "patient": "Пациент Один", "chart": "1", "iin": "", "phone": "7000000001", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": ""}]
        deleted = [{"appointment_date": "2026-06-01", "appointment_time": "09:00", "doctor": "Врач А", "patient": "Пациент Один", "patient_name": "пациент один", "phone": ""}]
        payload = nn_normalize.normalize_rows(master, registry, [], [], deleted, 1)
        self.assertEqual(payload["key_metrics"]["closed_appointments"], 0)

    def test_unpaid_clinical_treatment_with_primary_and_repeat_is_strict_suspicious(self):
        master = [{"name": "Пациент", "dob": "1980-01-01", "iin": "", "chart": "1", "phone": "", "note": "", "source_amount": 0}]
        registry = [
            {"date": "2026-06-01", "start": "09:00", "end": "09:30", "doctor": "Врач", "patient": "Пациент", "chart": "1", "iin": "", "phone": "", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": ""},
            {"date": "2026-06-10", "start": "09:00", "end": "09:30", "doctor": "Врач", "patient": "Пациент", "chart": "1", "iin": "", "phone": "", "repeat_field": "", "note": "контроль после операции", "visit_type": "Амбулаторно", "help_type": ""},
        ]
        medical = [{"date": "2026-06-05", "doctor": "Врач", "patient": "Пациент", "dob": "1980-01-01", "chart": "1", "complaint": "", "objective": "", "direction": "", "assignment": "", "recommendation": "", "treatment": "Протокол манипуляции: Пенная Склеротерапия"}]
        payload = nn_normalize.normalize_rows(master, registry, medical, [], [], 0)
        self.assertEqual(payload["key_metrics"]["strict_suspicious"], 1)

    def test_service_matrix_header_aliases_and_carry_forward(self):
        matrix = [
            ["Пациент", "Дата услуги", "Врач", "Наименование услуги", "Количество", "Сумма", "Комментарий"],
            ["Иванов Иван", "01.06.2026", "Врач", "Прием хирурга флеболога с УЗИ", 1, 4900, ""],
            ["", "", "", "Лазерное лечение варикоза", 1, 300000, "предоплата 17000"],
        ]
        rows = nn_normalize._service_rows_from_matrix(matrix)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["patient"], "Иванов Иван")
        self.assertEqual(rows[1]["date"], "2026-06-01")
        self.assertEqual(rows[1]["amount"], 300000)


if __name__ == "__main__":
    unittest.main()
