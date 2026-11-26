"""Regressions for evidence present in OCR but lost by goods extraction.

All text and amounts below are synthetic. The long-header layout represents
ordinary UPD columns, including their wrapped labels and index row; no private
document, supplier identity or real goods have been copied into this fixture.
"""
import copy
import unittest

import medical_supplies_ocr as ocr


def tsv(spec):
    result = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for line_no, (y, tokens) in enumerate(spec, 1):
        for word_no, token in enumerate(tokens, 1):
            x, text = token[:2]
            width = token[2] if len(token) == 3 else max(10, len(text) * 8)
            result.append("\t".join(map(str, (5, 1, 1, 1, line_no, word_no,
                x, y, width, 18, 96, text))))
    return "\n".join(result) + "\n"


HEADER = (100, [(20, "№"), (100, "Наименование", 200), (500, "Ед."),
                (650, "Количество"), (850, "Цена"), (1070, "Сумма")])


class LostGoodsTests(unittest.TestCase):
    def parse(self, spec):
        return ocr.parse_tsv(tsv(spec))

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


if __name__ == "__main__":
    unittest.main()
