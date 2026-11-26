"""Synthetic checks of draft totals and independent-document pagination."""
import copy
from decimal import Decimal
import unittest

import medical_supplies_ocr_quality as quality


def row(name="Материал", quantity="1", unit="шт", value="25.10", **overrides):
    return {"name": name, "quantity": quantity, "unit": unit, "value": value,
            "value_basis": "printed_gross", "folder_id": 3, "action": "new", **overrides}


def page(number="A-13", current=1, total=1, supplier="ООО «Пример»", **overrides):
    return {"header_candidate": {"document_type": "act", "counterparty": supplier,
            "number": number, "date": "2026-04-17", "pagination": {"page": current, "of": total},
            **overrides}}


class PageQualityTests(unittest.TestCase):
    def test_decimal_sum_is_exact_and_does_not_mutate_inputs(self):
        rows = [row(value="0.10"), row(value="0.20", quantity="0.001")]
        data = {"printed_total": "0,30", "total_basis": "printed_gross"}
        before = copy.deepcopy((data, rows))
        report = quality.summarize_page(data, rows)
        self.assertEqual((report["total_expected"], report["total_actual"], report["total_matches"]),
                         ("0.30", "0.30", True))
        self.assertEqual((report["complete_rows"], report["count"], report["issues"]), (2, 2, []))
        self.assertEqual((data, rows), before)

    def test_money_mismatch_is_reported_without_rounding_or_adjusting_rows(self):
        report = quality.summarize_page({"printed_total": "30.00", "total_basis": "printed_gross"},
                                        [row(value="29.99")])
        self.assertFalse(report["total_matches"])
        self.assertIn("не совпадает", " ".join(report["issues"]))

    def test_missing_quantity_unit_or_amount_prevents_partial_total_claim(self):
        for field in ("quantity", "unit", "value", "name"):
            with self.subTest(field=field):
                candidate = row(**{field: None})
                report = quality.summarize_page({"printed_total": "25.10", "total_basis": "printed_gross"}, [candidate])
                self.assertIsNone(report["total_actual"])
                self.assertIsNone(report["total_matches"])
                self.assertEqual(report["complete_rows"], 0)
                self.assertTrue(report["issues"])

    def test_skip_does_not_silently_change_original_total_scope(self):
        report = quality.summarize_page({"printed_total": "50.20", "total_basis": "printed_gross"},
                                        [row(), row(action="skip", folder_id=None)])
        self.assertEqual(report["total_actual"], "50.20")
        self.assertTrue(report["total_matches"])
        self.assertIn("исключённые", " ".join(report["issues"]))
        self.assertNotIn("Не выбран раздел", " ".join(report["issues"]))

    def test_zero_value_is_allowed_but_zero_quantity_is_not(self):
        for amount, quantity, complete in (("0", "1", 1), ("0", "0", 0)):
            with self.subTest(quantity=quantity):
                report = quality.summarize_page({"printed_total": "0", "total_basis": "printed_gross"},
                                                [row(value=amount, quantity=quantity)])
                self.assertEqual(report["complete_rows"], complete)
                self.assertEqual(report["total_matches"], True if complete else None)

    def test_net_tax_or_unknown_total_never_becomes_a_comparable_gross(self):
        for basis in (None, "printed_net", "tax", "unknown"):
            with self.subTest(basis=basis):
                report = quality.summarize_page({"printed_total": "25.10", "total_basis": basis}, [row()])
                self.assertIsNone(report["total_expected"])
                self.assertIsNone(report["total_matches"])
                self.assertTrue(report["issues"])

    def test_net_row_is_not_counted_even_when_number_matches(self):
        report = quality.summarize_page({"printed_total": "25.10", "total_basis": "printed_gross"},
                                        [row(value_basis="printed_net")])
        self.assertIsNone(report["total_actual"])
        self.assertIsNone(report["total_matches"])

    def test_manual_row_without_value_basis_can_be_checked(self):
        candidate = row()
        del candidate["value_basis"]
        report = quality.summarize_page({"printed_total": "25.10", "total_basis": "printed_single_total"}, [candidate])
        self.assertTrue(report["total_matches"])

    def test_does_not_guess_printed_total_from_other_data(self):
        report = quality.summarize_page({"lines": [{"text": "Код 25.10"}], "confidence": 99}, [row()])
        self.assertIsNone(report["total_expected"])
        self.assertIsNone(report["total_matches"])

    def test_negative_nonfinite_bad_grouping_and_overprecise_values_are_unknown(self):
        for value in ("-1", "NaN", "Infinity", Decimal("NaN"), "1e2", "25.101", "1 2.30", True, 25.1, "9" * 100):
            with self.subTest(value=value):
                report = quality.summarize_page({"printed_total": "25.10", "total_basis": "printed_gross"},
                                                [row(value=value)])
                self.assertEqual(report["complete_rows"], 0)
                self.assertIsNone(report["total_actual"])

    def test_large_legal_sum_and_limits(self):
        rows = [row(value="1000000000") for _ in range(200)]
        report = quality.summarize_page({"printed_total": "200000000000.00", "total_basis": "printed_gross"}, rows)
        self.assertEqual(report["total_actual"], "200000000000.00")
        self.assertTrue(report["total_matches"])
        for bad_rows in ([*rows, row()], [row(value="1000000000.01")]):
            report = quality.summarize_page({}, bad_rows)
            self.assertIsNone(report["total_actual"])
            self.assertTrue(report["issues"])

    def test_grouped_amount_and_source_diagnostics_survive(self):
        data = {"printed_total": "1\u00a0234,50", "total_basis": "printed_gross",
                "recognition_issues": ["Проверьте заголовок."],
                "header_candidate": {"issues": ["Проверьте заголовок."]}}
        report = quality.summarize_page(data, [row(value="1234.50", folder_id=None)])
        self.assertTrue(report["total_matches"])
        self.assertEqual(report["issues"].count("Проверьте заголовок."), 1)
        self.assertIn("Не выбран раздел", " ".join(report["issues"]))

    def test_empty_page_is_not_a_zero_goods_proof(self):
        report = quality.summarize_page({}, [])
        self.assertIsNone(report["total_actual"])
        self.assertTrue(report["issues"])

    def test_goods_view_omits_identity_warnings_but_keeps_money_and_source_checks(self):
        data = {"printed_total":"30.00","total_basis":"printed_gross",
                "recognition_issues":["Часть числовых ячеек не распознана."],
                "header_candidate":{"issues":["Дата не подтверждена.","Номер не распознан."]}}
        rows = [row(value="29.99")]
        before = copy.deepcopy((data,rows))
        report = quality.summarize_page(data,rows,include_identity=False)
        self.assertNotIn("Дата не подтверждена.",report["issues"])
        self.assertNotIn("Номер не распознан.",report["issues"])
        self.assertIn("Часть числовых ячеек не распознана.",report["issues"])
        self.assertFalse(report["total_matches"])
        self.assertIn("не совпадает"," ".join(report["issues"]))
        self.assertEqual((data,rows),before)
        self.assertIn("Дата не подтверждена.",quality.summarize_page(data,rows)["issues"])

    def test_saved_header_and_new_total_do_not_keep_old_proposal_warnings(self):
        data = {"printed_total": "25.10", "total_basis": "printed_gross", "header": {},
                "header_candidate": {"issues": ["Старое предупреждение о реквизитах."]},
                "quality": {"issues": ["Старое предупреждение о сумме."]}}
        self.assertEqual(quality.summarize_page(data, [row()])["issues"], [])
        self.assertFalse(quality.summarize_page({"no_items": True}, [])["issues"])
        report = quality.summarize_page({"no_items": True, "printed_total": "1", "total_basis": "printed_gross"}, [])
        self.assertTrue(report["issues"])


class DocumentCompletenessTests(unittest.TestCase):
    def test_two_pages_of_one_act_are_complete(self):
        pages = {"1": page(current=1, total=2), "2": page(current=2, total=2)}
        before = copy.deepcopy(pages)
        self.assertEqual(quality.document_issues(pages), [])
        self.assertEqual(pages, before)

    def test_other_act_cannot_complete_first_act(self):
        messages = quality.document_issues({"1": page(current=1, total=2), "2": page(number="B-14", current=1, total=1)})
        self.assertEqual(len(messages), 1)
        self.assertIn("не найдены страницы 2 из 2", messages[0])

    def test_same_number_with_different_supplier_does_not_merge(self):
        messages = quality.document_issues([page(current=1, total=2), page(current=2, total=2, supplier="ООО Другой")])
        self.assertEqual(len(messages), 2)
        self.assertTrue(all("не найдены" in message for message in messages))

    def test_duplicate_page_reported_even_when_number_set_complete(self):
        messages = quality.document_issues([page(current=1, total=2), page(current=2, total=2), page(current=2, total=2)])
        self.assertIn("повторяются номера", " ".join(messages))

    def test_conflicting_page_count_is_not_resolved_by_largest_number(self):
        messages = quality.document_issues([page(current=1, total=2), page(current=2, total=3)])
        self.assertIn("разное число страниц", " ".join(messages))

    def test_unknown_identity_cannot_group_by_part_or_filename(self):
        for field in quality.IDENTITY_FIELDS:
            with self.subTest(field=field):
                candidate = page(current=1, total=2, **{field: ""})
                candidate["header_candidate"]["part"] = 1
                messages = quality.document_issues([candidate, page(current=2, total=2)])
                self.assertIn("реквизиты распознаны не полностью", " ".join(messages))
                self.assertIn("не найдены страницы 1 из 2", " ".join(messages))

    def test_human_header_has_priority_even_if_empty(self):
        candidate = page(current=1, total=2)
        candidate["header"] = {}
        messages = quality.document_issues([candidate, page(current=2, total=2)])
        self.assertIn("реквизиты распознаны не полностью", " ".join(messages))

    def test_manual_header_reuses_extracted_pagination(self):
        first = page(current=1, total=2)
        first["header"] = {key: first["header_candidate"][key] for key in quality.IDENTITY_FIELDS}
        self.assertEqual(quality.document_issues([first, page(current=2, total=2)]), [])

    def test_unreadable_page_number_keeps_completeness_unknown(self):
        second = page(current=2, total=2)
        second["header_candidate"]["pagination"] = None
        messages = quality.document_issues([page(current=1, total=2), second])
        self.assertIn("не прочитана нумерация", " ".join(messages))
        self.assertIn("не найдены страницы 2 из 2", " ".join(messages))

    def test_invalid_pagination_is_bounded_and_does_not_raise(self):
        for pagination in ({"page": 0, "of": 2}, {"page": 3, "of": 2}, {"page": True, "of": 2},
                           {"page": 1, "of": 1000000000}, "1/2", {"page": "two", "of": 2}):
            with self.subTest(pagination=pagination):
                candidate = page()
                candidate["header_candidate"]["pagination"] = pagination
                self.assertIn("неоднозначно", " ".join(quality.document_issues([candidate])))
        self.assertIn("больше 20", " ".join(quality.document_issues([page() for _ in range(21)])))

    def test_number_punctuation_is_not_erased(self):
        messages = quality.document_issues([page(number="A-13", current=1, total=2), page(number="A13", current=2, total=2)])
        self.assertEqual(len(messages), 2)

    def test_spacing_and_quote_variants_can_match(self):
        self.assertEqual(quality.document_issues([page(supplier='ООО "Пример"', current=1, total=2),
                                                page(supplier=' ооо  «пример» ', current=2, total=2)]), [])

    def test_absent_pagination_or_identity_never_claims_completeness(self):
        for candidate in ({}, {"header_candidate": {}}, page(pagination=None)):
            with self.subTest(candidate=candidate):
                self.assertIn("Комплектность не подтверждена", " ".join(quality.document_issues([candidate])))
        self.assertEqual(quality.document_issues([{"no_items": True}]), [])


if __name__ == "__main__":
    unittest.main()
