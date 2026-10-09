from html.parser import HTMLParser
from pathlib import Path
import unittest
import re
import shutil
import subprocess
import tempfile
from jinja2 import Environment

ROOT = Path(__file__).resolve().parent


class FormStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.nested |= self.depth > 0
            self.depth += 1
            self.forms.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


class IndependentThreeUploadsTests(unittest.TestCase):
    def test_exact_label_and_three_separate_forms(self):
        html = (ROOT / "templates/uploads.html").read_text(encoding="utf-8")
        self.assertIn("2. Выручка по направлениям (новый оплаты)", html)
        self.assertIn("independent-upload-grid", html)
        forms = ("data-az-completed-upload-form", "data-az-paid-upload-form", "data-az-cash-upload-form")
        offsets = [html.index(marker) for marker in forms]
        self.assertEqual(offsets, sorted(offsets))
        self.assertLess(offsets[2], html.index("data-az-upload-form"))
        self.assertIn('name="services"', html)  # approved legacy path
        parsed = FormStructure()
        parsed.feed(html)
        self.assertFalse(parsed.nested)
        self.assertEqual(parsed.depth, 0)
        for marker in (*forms, "data-az-upload-form"):
            self.assertEqual(sum(1 for form in parsed.forms if marker in form), 1)

    def test_each_form_keeps_separate_preview_and_commit(self):
        html = (ROOT / "templates/uploads.html").read_text(encoding="utf-8")
        for name in ("completed", "paid-services", "cash", "clinical"):
            self.assertIn(f"/api/uploads/{name}/preview", html)
            self.assertIn(f"/api/uploads/{name}/commit", html)
        for kind, form in (("completed", "completedForm"), ("paid", "paidForm"),
                           ("cash", "cashForm"), ("clinical", "clinicalForm")):
            self.assertIn(f"preview('{kind}',{form})", html)

    def test_completed_only_cannot_activate_paid_or_reset_financial_sources(self):
        completed = (ROOT / "completed_upload.py").read_text(encoding="utf-8")
        self.assertNotIn("paid_services.", completed)
        self.assertNotIn("replace_service_month(", completed)
        self.assertNotIn('record["discountDataComplete"] = False', completed)
        self.assertIn('cash_payments.overlay_stored_month(', completed)
        self.assertIn('daily_upload._doctor_attribution(', completed)
        self.assertIn('SELECT data_date,completed_sha256,normalized_json', completed)
        preview = completed.split("def _preview():", 1)[1].split("def _read_normalized(", 1)[0]
        self.assertNotIn("conn.commit()", preview)
        self.assertNotIn("record_pending_providers(", preview)

    def test_prior_cash_paid_storage_and_clinical_paths_remain(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        old_clinical = (ROOT / "daily_upload.py").read_text(encoding="utf-8")
        paid = (ROOT / "paid_services_upload.py").read_text(encoding="utf-8")
        for module in ("daily_upload", "completed_upload", "cash_upload", "paid_services_upload"):
            self.assertIn(f"{module}.register_", server)
        self.assertIn('"/api/uploads/clinical/preview"', old_clinical)
        self.assertIn('"/api/uploads/clinical/commit"', old_clinical)
        self.assertIn("paid_services.store_snapshot(", paid)
        self.assertNotIn("paid_services.overlay_stored_month(", paid)
        self.assertNotIn("core.replace_service_month(", paid)

    def test_jinja_and_both_inline_javascript_blocks_parse(self):
        html = (ROOT / "templates/uploads.html").read_text(encoding="utf-8")
        Environment().parse(html)
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
        self.assertGreaterEqual(len(scripts), 2)
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for syntax verification")
        with tempfile.TemporaryDirectory() as folder:
            for index, script in enumerate(scripts):
                file = Path(folder) / f"inline-{index}.cjs"
                file.write_text(script, encoding="utf-8")
                result = subprocess.run([node, "--check", str(file)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
