from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "reports"

class ApprovedSourceRestoration(unittest.TestCase):
    def test_python_syntax(self):
        for name in ("server.py", "daily_upload.py", "paid_services_upload.py",
                     "attention.py", "finrez.py"):
            ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

    def test_paired_clinical_and_separate_mis_upload(self):
        html = (ROOT / "templates/uploads.html").read_text(encoding="utf-8")
        self.assertIn('data-az-upload-form', html)
        self.assertIn('name="completed"', html)
        self.assertIn('name="services"', html)
        self.assertIn('2. Выручка по направлениям', html)
        self.assertIn('3. Счета и оплаты', html)
        self.assertIn('4. Новый отчёт МИС', html)
        self.assertIn('/api/uploads/clinical/preview', html)
        self.assertIn('/api/uploads/clinical/commit', html)
        self.assertIn('data-az-paid-upload-form', html)
        self.assertNotIn('data-az-completed-upload-form', html)
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('completed_upload.register_completed_upload(app)', server)

    def test_paid_upload_is_storage_only(self):
        source = (ROOT / "paid_services_upload.py").read_text(encoding="utf-8")
        self.assertIn('paid_services.store_snapshot(', source)
        self.assertNotIn('core.replace_service_month(', source)
        self.assertNotIn('paid_services.overlay_stored_month(', source)
        self.assertNotIn('core._save_blob(', source)
        daily = (ROOT / "daily_upload.py").read_text(encoding="utf-8")
        self.assertNotIn('paid_services.overlay_record_map(', daily)
        self.assertNotIn('paid_services.overlay_stored_month(', daily)

    def test_existing_report_values_not_gated_by_new_mis(self):
        management = (REPORTS / "index.html").read_text(encoding="utf-8")
        dashboard = (REPORTS / "dashboard.html").read_text(encoding="utf-8")
        period = (REPORTS / "period-view.js").read_text(encoding="utf-8")
        forecast = (REPORTS / "forecast.html").read_text(encoding="utf-8")
        for source in (management, dashboard):
            self.assertNotIn('paidMode&&primary', source)
            self.assertNotIn('paidMode&&dentPrimary', source)
            self.assertNotIn('paidMode&&clinicPrimary', source)
        self.assertNotIn('paidDataComplete===true?num(', period)
        self.assertIn('FULL_MONTHS=ALL_MONTHS.filter(fullMonth)', forecast)
        self.assertNotIn('fullMonth(m)&&rec(m).paidDataComplete', forecast)

    def test_owner_approved_cash_and_economics_are_preserved(self):
        dashboard = (REPORTS / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('factTotal', dashboard)
        self.assertNotIn("metric('Нераспределённые ДС'", dashboard)
        finrez = (REPORTS / "finrez-economics-integrated.js").read_text(encoding="utf-8")
        self.assertIn('economicsRevenue', finrez)
        self.assertIn('discountFor = function', finrez)
        self.assertIn('grossRevenueFor = function', finrez)
        paid = (ROOT / "paid_services.py").read_text(encoding="utf-8")
        self.assertIn('service_payment_snapshots', paid)

if __name__ == "__main__":
    unittest.main()
