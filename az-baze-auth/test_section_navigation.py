import unittest

import section_navigation as nav


class SectionNavigationTests(unittest.TestCase):
    def test_section_map(self):
        cases = {
            "/reports/dashboard.html": ("Отчёты", "/reports/"),
            "/access-contacts/": ("Доступы и контакты", "/access-contacts/"),
            "/contacts/42/edit": ("Доступы и контакты", "/access-contacts/"),
            "/credentials/trash": ("Доступы и контакты", "/access-contacts/"),
            "/surveys/5/results": ("Опросы", "/surveys/"),
            "/uploads/": ("Загрузка данных", "/uploads/"),
            "/admin/users/2/edit": ("Управление доступом", "/admin"),
            "/knowledge/article.html": ("База знаний", "/knowledge/"),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                item = nav.section_for_path(path)
                self.assertEqual((item["label"], item["root"]), expected)

    def test_auth_and_public_survey_are_excluded(self):
        for path in ("/login", "/change-password", "/survey/", "/survey/?token=x"):
            with self.subTest(path=path):
                self.assertIsNone(nav.section_for_path(path))

    def test_root_section_is_current_and_nested_is_link(self):
        root = nav.render_navigation("/reports/")
        self.assertIn('aria-current="page">Отчёты</span>', root)
        self.assertNotIn('href="/reports/">Отчёты</a>', root)

        nested = nav.render_navigation("/reports/dashboard.html")
        self.assertIn('href="/reports/">Отчёты</a>', nested)
        self.assertIn('href="/">На главную</a>', nested)

    def test_duplicate_navigation_is_removed(self):
        source = """<!doctype html><html><head></head><body>
        <a class="btn" href="/reports/">К отчётам</a>
        <a class="btn" href="/">На главную</a>
        <a class="btn" href="/reports/finrez/">Финрез</a>
        </body></html>"""
        rendered = nav.inject_navigation(source, "/reports/dashboard.html")
        self.assertEqual(rendered.count("az-section-nav-style"), 1)
        self.assertEqual(rendered.count('href="/">На главную</a>'), 1)
        self.assertEqual(rendered.count('href="/reports/">Отчёты</a>'), 1)
        self.assertNotIn(">К отчётам</a>", rendered)
        self.assertIn('href="/reports/finrez/">Финрез</a>', rendered)

    def test_mobile_and_print_rules_exist(self):
        self.assertIn("@media(max-width:760px)", nav.NAV_CSS)
        self.assertIn("position:fixed", nav.NAV_CSS)
        self.assertIn("@media print", nav.NAV_CSS)
        self.assertIn(".az-section-nav{display:none!important}", nav.NAV_CSS)


if __name__ == "__main__":
    unittest.main()
