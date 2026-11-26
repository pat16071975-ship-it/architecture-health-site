"""Regressions for evidence present in OCR but lost by goods extraction.

All text and amounts below are synthetic. The long-header layout represents
ordinary UPD columns, including their wrapped labels and index row; no private
document, supplier identity or real goods have been copied into this fixture.
"""
import copy
import unittest

import medical_supplies_ocr as ocr


def tsv(spec, slope=0):
    result = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for line_no, (y, tokens) in enumerate(spec, 1):
        for word_no, token in enumerate(tokens, 1):
            x, text = token[:2]
            width = token[2] if len(token) >= 3 else max(10, len(text) * 8)
            height = token[3] if len(token) >= 4 else 18
            confidence = token[4] if len(token) >= 5 else 96
            result.append("\t".join(map(str, (5, 1, 1, 1, line_no, word_no,
                x, round(y + 18 - height + slope * x), width, height, confidence, text))))
    return "\n".join(result) + "\n"


HEADER = (100, [(20, "№"), (100, "Наименование", 200), (500, "Ед."),
                (650, "Количество"), (850, "Цена"), (1070, "Сумма")])


def act_header(value_label="Стоимость"):
    """Synthetic combined-heading act; the title is centered in a broad cell."""
    return [
        (100, [(680, "Артикул", 125), (820, "и", 15),
               (850, "наименование", 240), (1100, "товара", 110),
               (1770, "Ед.", 55), (1990, "Кол-во", 120),
               (2220, value_label, 180)]),
        (126, [(2220, "с", 10), (2245, "НДС", 65)]),
    ]


class LostGoodsTests(unittest.TestCase):
    def parse(self, spec, slope=0):
        return ocr.parse_tsv(tsv(spec, slope=slope))

    def assert_fragment_sources(self, parsed, row, fragments):
        """Every printed name fragment must remain linked to original OCR."""
        source_ids = set(row["source_lines"])
        lines = ocr.physical_lines(parsed["words"])
        for fragment in fragments:
            matching = [line for line in lines
                        if any(word[5] == fragment for word in line["tokens"])]
            self.assertEqual(len(matching), 1, fragment)
            self.assertIn(matching[0]["id"], source_ids, fragment)
        self.assertTrue(source_ids.issubset({line["id"] for line in parsed["lines"]}))

    def assert_raw_evidence_survives_refresh(self, parsed):
        legacy = copy.deepcopy(parsed)
        legacy["extractor_version"] = 2
        before = copy.deepcopy(legacy)
        refreshed = ocr.derive_page(legacy)
        self.assertEqual(legacy, before)
        self.assertEqual(refreshed["words"], before["words"])
        self.assertEqual(refreshed["lines"], before["lines"])
        self.assertEqual(refreshed["candidates"], parsed["candidates"])

    def split_amount_page(self, separator=True, confidence=86):
        tokens = [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                  (1780, "упак"), (2070, "36"),
                  (2290, "654", 54), (2380, "32", 24)]
        if separator:
            # The tiny mark is lowered and lies between the printed integer
            # and cents; its literal OCR evidence remains an apostrophe.
            tokens.append((2350, "'", 3, 5, confidence))
        return self.parse(act_header() + [
            (220, tokens),
            (330, [(70, "Итого"), (2290, "654,32")]),
        ], slope=0.03)

    def test_first_name_line_is_not_consumed_by_header(self):
        parsed = self.parse([
            HEADER,
            (170, [(20, "1"), (105, "Салфетки"), (195, "медицинские")]),
            (192, [(105, "стерильные"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
            (245, [(105, "Итого"), (1080, "289,20")]),
        ])
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual(row["name"], "Салфетки медицинские стерильные")
        self.assertEqual(row["source_lines"], [2, 3])
        self.assertEqual(row["source_line"], 3)
        self.assertEqual((row["quantity"], row["unit"], row["value"]), ("2", "упак", "289.20"))

    def test_three_line_name_stays_inside_its_printed_numbered_row(self):
        parsed = self.parse([
            HEADER,
            (170, [(20, "1"), (105, "Салфетки"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
            (192, [(105, "медицинские")]),
            (214, [(105, "стерильные")]),
            (236, [(20, "2"), (105, "Перчатки"), (505, "упак"), (680, "3"),
                   (870, "100,00"), (1080, "300,00")]),
            (285, [(105, "Итого"), (1080, "589,20")]),
        ])
        rows = parsed["candidates"]
        self.assertEqual([row["name"] for row in rows],
                         ["Салфетки медицинские стерильные", "Перчатки"])
        self.assertEqual([row["source_lines"] for row in rows], [[2, 3, 4], [5]])
        self.assertEqual([row["quantity"] for row in rows], ["2", "3"])
        self.assertEqual([row["value"] for row in rows], ["289.20", "300.00"])

    def test_unreadable_quantity_retains_the_good_without_guessing_it(self):
        parsed = self.parse([
            HEADER,
            (170, [(20, "1"), (105, "Салфетки"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
            (210, [(20, "2"), (105, "Перчатки"), (505, "упак"), (680, "l"),
                   (870, "100,00"), (1080, "100,00")]),
            (260, [(105, "Итого"), (1080, "389,20")]),
        ])
        self.assertEqual(len(parsed["candidates"]), 2)
        row = parsed["candidates"][1]
        self.assertEqual(row["name"], "Перчатки")
        self.assertIsNone(row["quantity"])
        self.assertEqual((row["price"], row["value"]), ("100.00", "100.00"))
        self.assertIn("упак l 100,00", parsed["lines"][2]["text"])

    def test_centered_name_heading_does_not_crop_left_words(self):
        parsed = self.parse([
            (100, [(350, "Наименование", 200), (650, "Ед."), (800, "Количество"),
                   (1000, "Цена"), (1250, "Сумма")]),
            (170, [(100, "Перчатки"), (210, "нитриловые"), (655, "упак"), (830, "2"),
                   (1020, "100,00"), (1260, "200,00")]),
            (245, [(100, "Итого"), (1260, "200,00")]),
        ])
        self.assertEqual(len(parsed["candidates"]), 1)
        self.assertEqual(parsed["candidates"][0]["name"], "Перчатки нитриловые")

    def test_article_continuation_does_not_discard_name_fragment(self):
        parsed = self.parse([
            (100, [(20, "№"), (100, "Наименование", 200), (450, "Артикул"),
                   (650, "Ед."), (800, "Количество"), (1000, "Цена"), (1250, "Сумма")]),
            (170, [(20, "1"), (100, "Перчатки"), (460, "12345"), (655, "упак"),
                   (830, "2"), (1020, "100,00"), (1260, "200,00")]),
            (192, [(100, "нитриловые"), (460, "XL")]),
            (245, [(100, "Итого"), (1260, "200,00")]),
        ])
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual(row["name"], "Перчатки нитриловые")
        self.assertEqual(row["source_lines"], [2, 3])
        self.assertNotIn("XL", row["name"])

    def test_long_upd_header_keeps_one_price_and_explicit_wrapped_gross(self):
        parsed = self.parse([
            (100, [(143, "Код"), (354, "№"), (442, "Наименование", 199), (712, "Код"),
                   (899, "Единица", 115), (1171, "Количество", 154),
                   (1414, "Цена", 66), (1491, "(тариф)", 103),
                   (1664, "Стоимость", 144), (2366, "Сумма", 89),
                   (2580, "Стоимость", 144), (2897, "Страна")]),
            (142, [(422, "товара"), (524, "(описание"), (885, "измерения"),
                   (1429, "за", 27), (1467, "единицу", 111),
                   (1912, "сумма", 82), (2005, "акциза"), (2203, "ставка")]),
            (184, [(425, "выполненных"), (618, "ра-"), (1432, "измерения")]),
            (206, [(817, "Код"), (940, "Условное")]),
            (226, [(441, "бот,"), (500, "оказанных"), (2595, "ных"), (2654, "прав"),
                   (2717, "с", 13), (2749, "на-", 39), (3171, "товары"), (3278, "или")]),
            (248, [(937, "обозначе-"), (2961, "именование")]),
            (268, [(435, "услуг),"), (533, "имущес-"), (1677, "прав"),
                   (1748, "без"), (1802, "на-"), (2599, "логом"), (2713, "всего")]),
            (290, [(935, "ние"), (991, "(наци-")]),
            (310, [(438, "твенного"), (567, "права"), (1677, "лога"), (1771, "всего")]),
            (332, [(935, "ональное)"), (2857, "код", 43)]),
            (352, [(3174, "товара,"), (3284, "подле-")]),
            (394, [(3183, "жащего"), (3294, "прос-")]),
            (435, [(3176, "леживаемости")]),
            (483, [(215, "А"), (363, "1"), (528, "la"), (721, "16"), (833, "2"),
                   (988, "2a"), (1241, "3"), (1496, "4"), (1752, "5"), (1996, "6"),
                   (2212, "7"), (2428, "8"), (2684, "9"), (2865, "10"), (3020, "10a")]),
            (548, [(171, "987654"), (341, "1"), (414, "Материал"), (817, "796"),
                   (897, "|шт"), (1538, "100.00", 85), (1792, "200.00", 86),
                   (1967, "Без"), (2021, "акциза"), (2254, "20%"),
                   (2473, "40.00", 70), (2734, "240.00", 88)]),
            (590, [(414, "медицинский")]),
            (632, [(413, "стерильный")]),
            (700, [(414, "Всего"), (2734, "240.00")]),
        ])
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual(row["name"], "Материал медицинский стерильный")
        self.assertEqual(row["unit"], "шт")
        self.assertIsNone(row["quantity"])
        self.assertEqual(row["price"], "100.00")
        self.assertEqual(row["value"], "240.00")
        self.assertEqual(row["value_basis"], "printed_gross")
        self.assertEqual(row["source_lines"], [15, 16, 17])

    def test_footer_numbered_text_is_not_added_as_goods(self):
        parsed = self.parse([
            HEADER,
            (170, [(20, "1"), (105, "Салфетки"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
            (214, [(105, "Итого"), (1080, "289,20")]),
            (236, [(20, "1"), (105, "Договор"), (230, "подписан")]),
            (258, [(20, "2"), (105, "Реквизиты"), (230, "сторон")]),
        ])
        self.assertEqual([row["name"] for row in parsed["candidates"]], ["Салфетки"])

    def test_version_two_refresh_preserves_raw_evidence_and_reviewed_rows(self):
        parsed = self.parse([
            HEADER,
            (170, [(20, "1"), (105, "Салфетки"), (195, "медицинские")]),
            (192, [(105, "стерильные"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
        ])
        parsed.update(extractor_version=2, candidates=[{"name": "стерильные"}],
                      rows=[{"name": "Ручная проверенная строка", "manual": True}],
                      checked=True, header={"number": "SYNTHETIC"})
        before = copy.deepcopy(parsed)
        refreshed = ocr.derive_page(parsed)
        self.assertEqual(parsed, before)
        for key in ("words", "lines", "rows", "checked", "header"):
            self.assertEqual(refreshed[key], before[key])
        self.assertEqual(refreshed["extractor_version"], ocr.EXTRACTOR_VERSION)
        self.assertEqual(refreshed["candidates"][0]["name"], "Салфетки медицинские стерильные")

    def test_combined_article_heading_does_not_define_the_names_left_edge(self):
        parsed = self.parse(act_header() + [
            (210, [(70, "7900001"), (230, "Материал"), (420, "учебный"),
                   (680, "упаковочный"), (1780, "упак"), (2070, "3"),
                   (2290, "321,45")]),
            (280, [(70, "7900002"), (230, "Набор"), (420, "контрольный"),
                   (1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (350, [(70, "Итого"), (2290, "1196,55")]),
        ])
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал учебный упаковочный", "Набор контрольный"])
        self.assertEqual([(r["quantity"], r["unit"], r["value"]) for r in rows],
                         [("3", "упак", "321.45"), ("7", "шт", "875.10")])
        self.assert_fragment_sources(parsed, rows[0], ["Материал", "учебный", "упаковочный"])
        self.assert_fragment_sources(parsed, rows[1], ["Набор", "контрольный"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_deskew_rejoins_names_and_numbers_across_mixed_physical_lines(self):
        parsed = self.parse(act_header() + [
            (210, [(70, "7900001"), (230, "Материал"), (720, "учебный"),
                   (1200, "длинный"), (1540, "хвост"), (1780, "упак"),
                   (2070, "3"), (2290, "321,45")]),
            (280, [(70, "7900002"), (230, "Набор"), (850, "контрольный"),
                   (1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (370, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.06)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал учебный длинный хвост", "Набор контрольный"])
        self.assertEqual([r["quantity"] for r in rows], ["3", "7"])
        self.assertEqual([r["value"] for r in rows], ["321.45", "875.10"])
        self.assert_fragment_sources(parsed, rows[0], ["Материал", "учебный", "длинный", "хвост"])
        self.assert_fragment_sources(parsed, rows[1], ["Набор", "контрольный"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_wrapped_name_keeps_all_fragments_with_vertically_centered_numbers(self):
        parsed = self.parse(act_header() + [
            (210, [(70, "7900001"), (230, "Материал"), (850, "учебный")]),
            (242, [(70, "мягкий"), (500, "длинномерный"), (1780, "упак"),
                   (2070, "3"), (2290, "321,45")]),
            (274, [(70, "вариант-А")]),
            (330, [(70, "7900002"), (230, "Набор"), (850, "контрольный")]),
            (346, [(1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (362, [(70, "вариант-Б")]),
            (430, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.04)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал учебный мягкий длинномерный вариант-А",
                          "Набор контрольный вариант-Б"])
        self.assertEqual([(r["quantity"], r["value"]) for r in rows],
                         [("3", "321.45"), ("7", "875.10")])
        self.assert_fragment_sources(parsed, rows[0],
                                     ["Материал", "учебный", "мягкий", "длинномерный", "вариант-А"])
        self.assert_fragment_sources(parsed, rows[1], ["Набор", "контрольный", "вариант-Б"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_unreadable_amount_heading_does_not_drop_goods_or_merge_numeric_columns(self):
        parsed = self.parse(act_header(value_label="???") + [
            (210, [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                   (1780, "упак"), (2070, "3"), (2290, "321,45")]),
            (280, [(70, "7900002"), (230, "Набор"), (850, "контрольный"),
                   (1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (370, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.025)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows], ["Материал учебный", "Набор контрольный"])
        self.assertEqual([r["quantity"] for r in rows], ["3", "7"])
        self.assertEqual([r["value"] for r in rows], ["321.45", "875.10"])
        self.assertTrue(all(r["price"] is None for r in rows))
        self.assert_fragment_sources(parsed, rows[0], ["Материал", "учебный"])
        self.assert_fragment_sources(parsed, rows[1], ["Набор", "контрольный"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_initial_orphan_name_without_article_or_numbers_is_retained_unresolved(self):
        parsed = self.parse(act_header() + [
            (210, [(720, "фрагмент"), (1100, "названия")]),
            (280, [(70, "7900002"), (230, "Набор"), (850, "контрольный"),
                   (1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (370, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.04)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows], ["фрагмент названия", "Набор контрольный"])
        self.assertIsNone(rows[0]["quantity"])
        self.assertFalse(rows[0]["unit"])
        self.assertIsNone(rows[0]["price"])
        self.assertIsNone(rows[0]["value"])
        self.assertEqual((rows[1]["quantity"], rows[1]["value"]), ("7", "875.10"))
        self.assert_fragment_sources(parsed, rows[0], ["фрагмент", "названия"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_total_label_on_same_physical_line_does_not_erase_last_goods_quantity(self):
        parsed = self.parse(act_header() + [
            (220, [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                   (1780, "упак"), (2070, "7"), (2290, "345,60")]),
            (300, [(70, "Итого"), (2290, "345,60")]),
        ], slope=0.04)
        shared = [line for line in ocr.physical_lines(parsed["words"])
                  if {"Итого", "7"}.issubset({w[5] for w in line["tokens"]})]
        self.assertEqual(len(shared), 1, "Fixture must reproduce mixed footer/quantity OCR evidence")
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual((row["name"], row["quantity"], row["unit"], row["value"]),
                         ("Материал учебный", "7", "упак", "345.60"))
        self.assertIn(shared[0]["id"], row["source_lines"])
        self.assert_fragment_sources(parsed, row, ["Материал", "учебный"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_tall_ocr_word_keeps_its_baseline_and_does_not_move_to_previous_good(self):
        parsed = self.parse(act_header() + [
            (210, [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                   (1780, "упак"), (2070, "3"), (2290, "321,45")]),
            (270, [(70, "7900002"), (230, "Набор"),
                   (1000, "КРУПНЫЙ", 120, 45), (1400, "контрольный"),
                   (1780, "шт"), (2070, "7"), (2290, "875,10")]),
            (350, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.03)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал учебный", "Набор КРУПНЫЙ контрольный"])
        self.assertEqual([(r["quantity"], r["value"]) for r in rows],
                         [("3", "321.45"), ("7", "875.10")])
        self.assert_fragment_sources(parsed, rows[1], ["Набор", "КРУПНЫЙ", "контрольный"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_unit_word_inside_name_does_not_move_the_unit_column_left(self):
        header = act_header()
        header[0][1][4] = (1850, "Ед.", 55)
        spec = header + [
            (210, [(70, "7900001"), (230, "Материал"), (700, "контрольный"),
                   (1650, "12"), (1740, "ШТ"), (1800, "В")]),
            (230, [(1850, "упак"), (2070, "3"), (2290, "321,45")]),
            (250, [(70, "упаковке")]),
            (310, [(70, "7900002"), (230, "Набор"), (850, "учебный"),
                   (1850, "шт"), (2070, "7"), (2290, "875,10")]),
            (390, [(70, "Итого"), (2290, "1196,55")]),
        ]
        # Scale text height so a word in the broad name cell lies within the
        # possible unit-column search window, as it does in a normal scan.
        spec = [(y, [(t[0], t[1], t[2] if len(t) >= 3 else max(10, len(t[1])*8), 30)
                     for t in tokens]) for y, tokens in spec]
        parsed = self.parse(spec, slope=0.03)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал контрольный 12 ШТ В упаковке", "Набор учебный"])
        self.assertEqual([(r["quantity"], r["unit"], r["value"]) for r in rows],
                         [("3", "упак", "321.45"), ("7", "шт", "875.10")])
        self.assert_fragment_sources(parsed, rows[0],
                                     ["Материал", "контрольный", "12", "ШТ", "В", "упаковке"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_tiny_semicolon_below_quantity_is_preserved_without_erasing_clean_digits(self):
        parsed = self.parse(act_header() + [
            (220, [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                   (1780, "упак"), (2070, "36"), (2290, "720,90")]),
            (232, [(2060, ";", 3, 5, 18)]),
            (330, [(70, "Итого"), (2290, "720,90")]),
        ], slope=0.03)
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual((row["name"], row["quantity"], row["unit"], row["value"]),
                         ("Материал учебный", "36", "упак", "720.90"))
        self.assert_fragment_sources(parsed, row, ["Материал", "учебный", "36", ";"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_product_colon_and_unreadable_quantity_do_not_look_like_damaged_total(self):
        parsed = self.parse(act_header() + [
            (210, [(70, "7900001"), (230, "Материал"), (850, "учебный"),
                   (1780, "упак"), (2070, "3"), (2290, "321,45")]),
            (280, [(70, "7900002"), (230, "Комплект:"), (850, "контрольный"),
                   (1780, "шт"), (2070, "l"), (2290, "875,10")]),
            (350, [(70, "Итого"), (2290, "1196,55")]),
        ], slope=0.03)
        rows = parsed["candidates"]
        self.assertEqual([r["name"] for r in rows],
                         ["Материал учебный", "Комплект: контрольный"])
        self.assertIsNone(rows[1]["quantity"])
        self.assertEqual((rows[1]["unit"], rows[1]["value"]), ("шт", "875.10"))
        self.assert_fragment_sources(parsed, rows[1], ["Комплект:", "контрольный", "l"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_lowered_printed_decimal_mark_keeps_its_literal_ocr_evidence(self):
        parsed = self.split_amount_page()
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual((row["name"], row["quantity"], row["value"]),
                         ("Материал учебный", "36", "654.32"))
        self.assert_fragment_sources(parsed, row, ["Материал", "учебный", "654", "'", "32"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_low_confidence_decimal_mark_does_not_turn_into_a_confirmed_amount(self):
        parsed = self.split_amount_page(confidence=20)
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual((row["name"], row["quantity"]), ("Материал учебный", "36"))
        self.assertIsNone(row["value"])
        self.assert_fragment_sources(parsed, row, ["654", "'", "32"])
        self.assert_raw_evidence_survives_refresh(parsed)

    def test_missing_decimal_mark_is_not_inferred_from_two_separate_digit_tokens(self):
        parsed = self.split_amount_page(separator=False)
        self.assertEqual(len(parsed["candidates"]), 1)
        row = parsed["candidates"][0]
        self.assertEqual((row["name"], row["quantity"]), ("Материал учебный", "36"))
        self.assertIsNone(row["value"])
        self.assert_fragment_sources(parsed, row, ["654", "32"])
        self.assert_raw_evidence_survives_refresh(parsed)


if __name__ == "__main__":
    unittest.main()
