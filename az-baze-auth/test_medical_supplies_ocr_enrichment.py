"""Synthetic identity/classification examples; no private source documents."""
import copy
import unittest

import medical_supplies_ocr_enrichment as enrich


def folders():
    paths = {
        "Расходные материалы": ["СИЗ", "ЦСО", "Гигиена", "Разное"],
        "Хоз/Канц товары": ["Канц.Товары", "Хоз.товары", "Товары для пациентов"],
        "Дезинфекция": [],
    }
    result = []
    for root, children in paths.items():
        parent = len(result) + 41
        result.append({"id": parent, "name": root, "parent_id": None})
        for name in children:
            result.append({"id": len(result) + 41, "name": name, "parent_id": parent})
    return result


def item(name="Салфетки тестовые", unit="упак", ident=7, folder_id=43, excluded=False):
    return dict(id=ident, name=name, unit=unit, folder_id=folder_id, excluded=excluded)


def row(name="Салфетки тестовые", unit="УПАК", **extra):
    return dict(name=name, unit=unit, quantity="3", price=None, value="417.20",
                action="unresolved", item_id=None, folder_id=None, **extra)


def header(*lines, page=1):
    return enrich.header_proposal(lines=[{"id": i, "text": text} for i, text in enumerate(lines, 1)], page=page)


class ClassificationTests(unittest.TestCase):
    def classify(self, rows, items=(), history=(), fs=None):
        return enrich.enrich_rows(rows, items, folders() if fs is None else fs, history)

    def test_exact_catalog_match_canonicalizes_only_unit_spelling(self):
        original = row(name="САЛФЕТКИ, тестовые", unit="упаковка")
        before = copy.deepcopy(original)
        result = self.classify([original], [item()])[0]
        self.assertEqual(original, before)
        self.assertEqual((result["action"], result["item_id"], result["folder_id"], result["unit"]),
                         ("existing", 7, 43, "упак"))
        self.assertEqual((result["quantity"], result["price"], result["value"]), ("3", None, "417.20"))
        self.assertEqual(result["classification"]["original_unit"], "упаковка")

    def test_pack_and_piece_never_match_by_fuzzy_name(self):
        result = self.classify([row(unit="шт")], [item()])[0]
        self.assertIsNone(result["item_id"])
        self.assertEqual(result["unit"], "шт")

    def test_two_exact_materials_are_not_silently_resolved_by_id(self):
        result = self.classify([row()], [item(), item(ident=8)])[0]
        self.assertEqual(result["action"], "unresolved")
        self.assertEqual(result["classification"]["source"], "ambiguous_catalog")

    def test_excluded_exact_material_is_not_recreated_by_semantic_rule(self):
        result = self.classify([row(name="Бахилы одноразовые")],
                               [item(name="Бахилы одноразовые", excluded=True)])[0]
        self.assertEqual(result["action"], "unresolved")
        self.assertIsNone(result["folder_id"])

    def test_catalog_missing_folder_does_not_copy_stale_id(self):
        result = self.classify([row()], [item(folder_id=9999)])[0]
        self.assertEqual(result["action"], "existing")
        self.assertIsNone(result["folder_id"])

    def test_manual_choices_preserved_even_if_unavailable_or_empty(self):
        source = [row(), row(name="Другой")]
        source[0].update(action="skip", note="Оператор исключил", folder_id=999)
        source[1].update(action="new", folder_id=None, note="Оставить пока без раздела")
        self.assertEqual(self.classify(source, [item()]), source)

    def test_checked_history_reuses_only_classification(self):
        previous = row()
        previous.update(action="new", folder_id=43, quantity="999", price="3.00", value="2997.00")
        result = self.classify([row()], history=[{"checked": True, "rows": [previous]}])[0]
        self.assertEqual((result["action"], result["folder_id"]), ("new", 43))
        self.assertEqual((result["quantity"], result["price"], result["value"]), ("3", None, "417.20"))
        self.assertIsNone(result["item_id"])

    def test_unchecked_skipped_and_unit_mismatched_history_does_not_train(self):
        previous = row()
        previous.update(action="new", folder_id=43)
        for history in ([{"checked": False, "rows": [previous]}],
                        [{"checked": True, "rows": [{**previous, "action": "skip"}]}],
                        [{"checked": True, "rows": [{**previous, "unit": "шт"}]}]):
            with self.subTest(history=history):
                self.assertEqual(self.classify([row()], history=history)[0]["action"], "unresolved")

    def test_conflicting_history_does_not_fall_through_to_rule(self):
        source = row(name="Бахилы одноразовые")
        previous = [{**source, "checked": True, "action": "new", "folder_id": fid} for fid in (43, 44)]
        result = self.classify([source], history=previous)[0]
        self.assertEqual(result["action"], "unresolved")
        self.assertEqual(result["classification"]["source"], "ambiguous_history")

    def test_stale_history_choice_remains_a_conflict(self):
        source = row(name="Бахилы одноразовые")
        history = [{**source, "checked": True, "action": "new", "folder_id": fid} for fid in (43, 999)]
        self.assertEqual(self.classify([source], history=history)[0]["action"], "unresolved")

    def test_semantic_rules_use_existing_paths_and_keep_purpose_unspecified(self):
        cases = {
            "Бахилы н/с 30 пар/уп": "СИЗ",
            "Перчатки медицинские смотровые 100 шт": "СИЗ",
            "Пакет для стерилизации 100 шт": "ЦСО",
            "Салфетки сухие д/дез. сменный блок": "Дезинфекция",
            "Дезинфицирующее средство для поверхностей": "Дезинфекция",
            "Палочки ватные": "Гигиена",
            "Электроды для ЭКГ": "Разное",
            "Шпатель терапевтический деревянный": "Разное",
            "Простыня н/с 200x70": "Разное",
            "Бумага A4 500 л": "Канц.Товары",
            "Ролики для касс 57 мм": "Канц.Товары",
            "Файл-вкладыш A4": "Канц.Товары",
            "Мешки для мусора 60 л": "Хоз.товары",
            "Салфетки для экранов": "Хоз.товары",
            "Салфетки бумажные": "Хоз.товары",
            "Чай зелёный": "Хоз/Канц товары",
            "Молоко 1 л": "Хоз/Канц товары",
        }
        fs = folders()
        by_id = {f["id"]: f["name"] for f in fs}
        for name, expected in cases.items():
            with self.subTest(name=name):
                actual = self.classify([row(name=name)], fs=fs)[0]
                self.assertEqual(actual["action"], "new")
                self.assertEqual(by_id[actual["folder_id"]], expected)

    def test_missing_renamed_or_duplicate_folder_is_not_created_or_guessed(self):
        fs = folders()
        for altered in ([], [{**f, "name": "Другое"} for f in fs],
                        fs + [{"id": 999, "name": "СИЗ", "parent_id": 41}]):
            with self.subTest(folders=altered):
                self.assertIsNone(self.classify([row(name="Бахилы")], fs=altered)[0]["folder_id"])

    def test_folder_queries_with_parent_name_are_supported(self):
        fs = [{"id": 230, "name": "СИЗ", "parent": "Расходные материалы"}]
        self.assertEqual(self.classify([row(name="Бахилы")], fs=fs)[0]["folder_id"], 230)

    def test_narrow_commodity_ocr_variants_do_not_weaken_catalog_identity(self):
        cases = [("Простыня H/C белая 180x90 20 шт", "Разное"),
                 ("Простыня Н/C зелёная 160x80 15 шт", "Разное"),
                 ("Файл-вкладьш А5 30 мкм", "Канц.Товары")]
        by_id = {f["id"]: f["name"] for f in folders()}
        for name, expected in cases:
            with self.subTest(name=name):
                actual = self.classify([row(name=name)])[0]
                self.assertEqual(by_id[actual["folder_id"]], expected)
        exact = item(name="Файл-вкладыш А5 30 мкм")
        proposed = self.classify([row(name="Файл-вкладьш А5 30 мкм")], [exact])[0]
        self.assertIsNone(proposed["item_id"])
        self.assertEqual(proposed["action"], "new")
        self.assertIsNone(self.classify([row(name="Ткань H/C 180x90")])[0]["folder_id"])

    def test_ambiguous_description_and_similar_words_remain_unresolved(self):
        for name in ("Бумага для стерилизации A4", "Перчатки хозяйственные хлопчатобумажные",
                     "Перчатки хозяйственные нитриловые", "Бумага цветная для творчества",
                     "Салфетки универсальные", "Чайник электрический"):
            with self.subTest(name=name):
                self.assertIsNone(self.classify([row(name=name)])[0]["folder_id"])


class HeaderProposalTests(unittest.TestCase):
    def test_buyer_first_is_not_the_supplier(self):
        result = header("Акт № AB-317", "приёма-передачи товара", "24.10.2026",
                        'ООО "Покупатель Тест" ИНН 123, далее по тексту «Покупатель»,',
                        'и ООО "Поставщик Тест" ИНН 456, далее по тексту «Поставщик».',
                        'Страница 1 из 2', page=7)
        self.assertEqual(result["document_type"], "act")
        self.assertEqual(result["counterparty"], "ООО «Поставщик Тест»")
        self.assertEqual((result["number"], result["date"], result["part"]), ("AB-317", "2026-10-24", 7))
        self.assertEqual(result["pagination"], {"page": 1, "of": 2})
        self.assertTrue(result["evidence"]["number"])
        self.assertNotIn("checked", result)

    def test_supplier_explicit_and_shipping_roles_ignore_receiver(self):
        for text in ('Поставщик: ООО «Компания А»; Покупатель: ООО «Компания Б»',
                     'Грузоотправитель Обособленное подразделение ООО «Компания А»\nГрузополучатель ООО «Компания Б»'):
            with self.subTest(text=text):
                self.assertEqual(header("Накладная № 101 от 01.11.2026", text)["counterparty"], "ООО «Компания А»")

    def test_contradictory_supplier_identities_stay_unknown(self):
        result = header("Счёт на оплату № 14", 'Поставщик: ООО «Компания А»', 'Поставщик: ООО «Компания Б»')
        self.assertEqual(result["counterparty"], "")
        self.assertIn("Поставщик распознан неоднозначно.", result["issues"])

    def test_first_company_without_supplier_role_is_not_selected(self):
        result = header("Акт № 333", 'ООО «Компания А» ИНН 123', "01.11.2026")
        self.assertEqual(result["counterparty"], "")

    def test_interleaved_ocr_cannot_attribute_later_supplier_role_to_buyer(self):
        result = header("Акт № 333", 'ООО «Компания А» ИНН 123,',
                        'далее по тексту «Покупатель», с одной стороны и ООО',
                        '456, стороны, «Компания Б» ИНН', 'далее по тексту «Поставщик».')
        self.assertEqual(result["counterparty"], "")

    def test_quoted_party_role_after_broken_company_form_is_not_company_name(self):
        result = header("Акт № 333", '«Поставщик», с другой стороны, и ООО',
                        '«Сторона», стороны, вместе «Компания Б» ИНН')
        self.assertEqual(result["counterparty"], "")

    def test_footer_order_date_and_body_invoice_do_not_change_act_identity(self):
        result = header("Акт № AX-17", "03.11.2026", "Товарные строки",
                        "Счёт-фактура № INV-99 от 04.11.2026", "Текст соглашения",
                        "Акт приёма-передачи AX-17 от 03.11.2026 По З-с 123 от 01.11.2026")
        self.assertEqual((result["document_type"], result["number"], result["date"]), ("act", "AX-17", "2026-11-03"))

    def test_conflicting_dates_are_not_fixed_to_majority_or_current_year(self):
        result = header("Акт № AX-17", "03.11.2025",
                        "Акт приёма-передачи AX-17 от 03.11.2026")
        self.assertIsNone(result["date"])
        self.assertIn("Распознаны разные даты одного документа.", result["issues"])

    def test_uncertain_identifier_prefix_not_transliterated(self):
        result = header("Акт № $01234567890123", "01.11.2026",
                        "Акт приёма-передачи 501234567890123 от 01.11.2026")
        self.assertEqual(result["number"], "")
        self.assertTrue(result["issues"])

    def test_numeric_identifier_remains_numeric_and_split_groups_join(self):
        result = header("Акт № 123456 789012", "01.11.2026",
                        "Акт приёма-передачи 123456789012 от 01.11.2026")
        self.assertEqual(result["number"], "123456789012")

    def test_invoice_and_upd_types_are_distinct(self):
        self.assertEqual(header("Счёт-фактура № INV/12 от 05.11.2026")["document_type"], "invoice")
        self.assertEqual(header("Универсальный передаточный документ № U-12")["document_type"], "upd")

    def test_invalid_calendar_date_and_order_only_date_not_used(self):
        self.assertIsNone(header("Акт № A-1 от 31.02.2026")["date"])
        self.assertIsNone(header("Акт № A-1", "Заказ покупателя 999 от 05.11.2026")["date"])

    def test_ocr_three_in_order_label_does_not_make_an_order_date_a_document_date(self):
        result = header("Акт № A-1", "06.11.2026", "Товары", "Акт приёма-передачи A-1",
                        "По 3-c 123456 от 05.11.2026 Страница 1 из 1")
        self.assertEqual(result["date"], "2026-11-06")

    def test_non_document_text_does_not_make_a_header(self):
        result = header("Составили настоящий акт о нижеследующем", "Заказ № 123 от 01.11.2026")
        self.assertEqual(result["document_type"], "unknown")
        self.assertEqual(result["number"], "")
        self.assertIsNone(result["date"])

    def test_word_boxes_and_page_dict_inputs_are_supported_and_unmodified(self):
        words = [[10, 10, 30, 20, 95, "Акт"], [45, 10, 20, 20, 95, "№"],
                 [70, 10, 50, 20, 95, "A-2"], [10, 40, 100, 20, 95, "05.11.2026"]]
        before = copy.deepcopy(words)
        self.assertEqual(enrich.header_proposal(words)["number"], "A-2")
        self.assertEqual(words, before)
        result = enrich.header_proposal({"lines": [{"id": 9, "text": "Акт № B-3 от 06.11.2026"}], "words": []})
        self.assertEqual(result["evidence"]["number"][0]["line"], 9)
        self.assertEqual(result["date"], "2026-11-06")

    def test_complete_aligned_metadata_lines_win_but_keep_repeated_date_conflict(self):
        raw = [
            {"id": 1, "text": "Акт № $01234567890123"},
            {"id": 2, "text": 'ООО «Компания А» далее по тексту «Покупатель»'},
            {"id": 3, "text": "03.11.2026"},
        ]
        aligned = [
            {"id": 4, "text": "Акт № Z-21"},
            {"id": 5, "text": "03.11.2025"},
            {"id": 6, "text": 'Поставщик: ООО «Компания Б»; Покупатель: ООО «Компания А»'},
            {"id": 70, "text": "Акт приёма-передачи Z-21 от 03.11.2026 По З-с 99 от 01.11.2026"},
        ]
        source = {"lines": raw, "metadata_lines": aligned}
        before = copy.deepcopy(source)
        result = enrich.header_proposal(source)
        self.assertEqual(source, before)
        self.assertEqual(result["number"], "Z-21")
        self.assertEqual(result["counterparty"], "ООО «Компания Б»")
        self.assertIsNone(result["date"])
        self.assertIn("Распознаны разные даты одного документа.", result["issues"])
        self.assertEqual([e["line"] for e in result["evidence"]["date"]], [5, 70])

    def test_empty_aligned_metadata_uses_existing_raw_line_fallback(self):
        source = {"metadata_lines": [], "lines": [{"id": 1, "text": "Акт № B-5 от 06.11.2026"}]}
        result = enrich.header_proposal(source)
        self.assertEqual((result["number"], result["date"]), ("B-5", "2026-11-06"))

    def test_cross_read_agreement_retains_date_pagination_and_aligned_supplier(self):
        aligned = [{"id": 1, "text": "Акт № Z-21 от 03.11.2026"},
                   {"id": 2, "text": 'Поставщик: ООО «Компания Б»'},
                   {"id": 30, "text": "Страница 1 из 2"}]
        first = [{"id": 4, "text": "Акт № $01234567890123"},
                 {"id": 5, "text": "03.11.2026"},
                 {"id": 60, "text": "Страница 1 из 2"}]
        result = enrich.header_proposal({"metadata_lines": aligned, "metadata_first_pass": first})
        self.assertEqual((result["date"], result["pagination"]), ("2026-11-03", {"page": 1, "of": 2}))
        self.assertEqual(result["counterparty"], "ООО «Компания Б»")
        self.assertEqual(result["number"], "Z-21")
        self.assertEqual(result["issues"], [])
        self.assertEqual({e["read"] for e in result["evidence"]["date"]}, {"aligned", "first_pass"})

    def test_cross_read_conflicts_never_choose_confident_looking_year_or_page_count(self):
        aligned = [{"id": 1, "text": "Акт № Z-21 от 03.11.2023"},
                   {"id": 2, "text": 'Поставщик: ООО «Компания Б»'},
                   {"id": 30, "text": "Страница 1 из 4"}]
        first = [{"id": 4, "text": "Акт № Z-21 от 03.11.2026"},
                 {"id": 60, "text": "Страница 1 из 1"}]
        source = {"metadata_lines": aligned, "metadata_first_pass": first}
        before = copy.deepcopy(source)
        result = enrich.header_proposal(source)
        self.assertEqual(source, before)
        self.assertIsNone(result["date"])
        self.assertIsNone(result["pagination"])
        self.assertEqual(result["evidence"]["date_cross_read"], {"aligned": "2023-11-03", "first_pass": "2026-11-03"})
        self.assertEqual(result["evidence"]["pagination_cross_read"],
                         {"aligned": {"page": 1, "of": 4}, "first_pass": {"page": 1, "of": 1}})
        self.assertIn("Дата не подтверждена двумя чтениями оригинала.", result["issues"])
        self.assertIn("Нумерация страниц не подтверждена двумя чтениями оригинала.", result["issues"])

    def test_unknown_first_read_cannot_be_replaced_with_an_unconfirmed_second_read(self):
        aligned = [{"id": 1, "text": "Акт № Z-21 от 03.11.2026"},
                   {"id": 2, "text": 'Поставщик: ООО «Компания Б»'},
                   {"id": 30, "text": "Страница 1 из 2"}]
        for first in ([], [{"id": 1, "text": "Нечитаемые дата и нумерация"}],
                      [{"id": 1, "text": "Акт № Z-21 от 03.11.2025"},
                       {"id": 40, "text": "Акт приёма-передачи Z-21 от 03.11.2026"}]):
            with self.subTest(first=first):
                result = enrich.header_proposal({"metadata_lines": aligned, "metadata_first_pass": first})
                self.assertIsNone(result["date"])
                self.assertIsNone(result["pagination"])
                self.assertEqual(result["counterparty"], "ООО «Компания Б»")
                self.assertEqual(result["document_type"], "act")


if __name__ == "__main__":
    unittest.main()
