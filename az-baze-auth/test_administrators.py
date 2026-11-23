"""AZ administrators independent worktime/pay tests. No production data."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BOOT = tempfile.TemporaryDirectory()
os.environ.setdefault("AZBAZE_SECRET_KEY", "administrators-local-tests-only")
os.environ["AZBAZE_DB"] = str(Path(BOOT.name) / "seed.db")
os.environ["AZBAZE_CREDENTIALS_KEY"] = str(Path(BOOT.name) / "credentials.key")
os.environ["AZBAZE_SITE_ROOT"] = str(Path(__file__).parents[1])
import app as core
core.init_db_file()
import server
import administrators as adm


class AdministratorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        shutil.copyfile(Path(BOOT.name) / "seed.db", root / "auth.db")
        (root / "index.html").write_bytes((Path(__file__).parents[1] / "az-baze/index.html").read_bytes())
        for obj, field, value in (
            (core, "DB_PATH", root / "auth.db"),
            (core, "SITE_ROOT", root),
            (core, "OWNER_EMAIL", "owner@example.test"),
        ):
            patcher = patch.object(obj, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.conn = sqlite3.connect(core.DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        adm.init_schema(self.conn)
        self.conn.commit()
        for uid, email, is_admin in (
            (1, "owner@example.test", 1),
            (2, "systemadmin@example.test", 1),
            (3, "viewer@example.test", 0),
            (4, "hours@example.test", 0),
            (5, "supervisor@example.test", 0),
        ):
            self.conn.execute(
                "INSERT INTO users(id,email,full_name,password_hash,is_admin,active,"
                "must_change_password,created_at,updated_at)"
                " VALUES(?,?,?,'x',?,1,0,'t','t')",
                (uid, email, "Тест " + str(uid), is_admin),
            )
        self.conn.executemany(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            [(3, "admins_view"), (4, "admins_hours"),
             (5, "admins_payroll"), (5, "admins_close")],
        )
        self.conn.commit()
        self.web = server.app
        self.web.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    def client(self, uid=None):
        client = self.web.test_client()
        if uid:
            with client.session_transaction() as s:
                s["user_id"] = uid
                s["csrf"] = "admin-test-csrf"
        return client

    def post(self, uid, endpoint, **fields):
        month = fields.pop("month", "2026-11")
        version = fields.pop("version", adm.month_state(self.conn, month)["version"])
        data = dict(csrf="admin-test-csrf", month=month, version=str(version))
        data.update(fields)
        return self.client(uid).post("/administrators/" + endpoint, data=data)

    def staff(self, rate="200,00", name="Иванова Анна"):
        response = self.post(1, "settings", full_name=name, rate=rate, active="1")
        self.assertEqual(response.status_code, 303)
        return self.conn.execute("SELECT id FROM az_admin_staff ORDER BY id DESC LIMIT 1").fetchone()["id"]

    def snapshot(self):
        return "\n".join(self.conn.iterdump())

    def test_access_and_permissions_explicit_for_system_admin(self):
        for uid, code in ((None, 302), (1, 200), (2, 403), (3, 200), (4, 200), (5, 200)):
            self.assertEqual(self.client(uid).get("/administrators/").status_code, code)
        self.assertIn('id="administrators"', self.client(1).get("/").text)
        self.assertIn('id="administrators"', self.client(3).get("/").text)
        self.assertNotIn('id="administrators"', self.client(2).get("/").text)
        self.assertEqual(self.post(3, "settings", full_name="Чужой", rate="100", active="1").status_code, 403)
        self.assertEqual(self.post(3, "hours", staff_id="1", work_date="2026-11-01", value="10").status_code, 403)
        self.assertEqual(self.post(4, "payroll", staff_id="1", standards="0", conversion="0", category="0", plan="0", extras="0").status_code, 403)
        self.assertEqual(self.post(5, "month", action="reopen").status_code, 403)

    def test_get_and_empty_default_do_not_write_database(self):
        before = self.snapshot()
        for tab in ("table", "payroll", "settings"):
            res = self.client(1).get(f"/administrators/?tab={tab}&month=2026-11")
            self.assertEqual(res.status_code, 200)
            self.assertIn("Администраторы", res.text)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.client(1).get("/administrators/?month=bad").status_code, 400)

    def test_default_off_hours_next_to_name_and_holiday_colors(self):
        sid = self.staff()
        res = self.client(4).get("/administrators/?month=2026-11")
        self.assertIn('class="az-total"', res.text)
        self.assertIn("Иванова Анна", res.text)
        self.assertIn('<option value="off" selected>вых</option>', res.text)
        self.assertIn("az-weekend", res.text)
        self.assertIn("az-holiday", res.text)
        self.assertEqual(self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="12").status_code, 303)
        row = adm.read_rows(self.conn, "2026-11")[0]
        self.assertEqual(row["minutes"], 720)
        self.assertEqual(row["days"][4], "12")
        self.assertEqual(row["hours"], "12")
        self.assertEqual(self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="off").status_code, 303)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["minutes"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM az_admin_days").fetchone()[0], 0)

    def test_payroll_formula_and_individual_months(self):
        sid = self.staff()
        self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="12")
        self.assertEqual(self.post(5, "payroll", staff_id=str(sid), standards="1800,00",
                                   conversion="900", category="200", plan="300", extras="100").status_code, 303)
        row = adm.read_rows(self.conn, "2026-11")[0]
        self.assertEqual(row["calculated"],
                         dict(base=240000, standards=12000, conversion=6000,
                              category=20000, plan=30000, extras=10000))
        self.assertEqual(row["total"], 318000)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["bonus"]["standards"], 0)
        html = self.client(1).get("/administrators/?tab=payroll&month=2026-11").text
        self.assertIn("3 180,00", html)
        self.assertIn("Фонд оплаты труда административного штата", html)

    def test_close_reopen_and_preserve_historical_snapshot(self):
        sid = self.staff()
        self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="12")
        self.post(5, "payroll", staff_id=str(sid), standards="1800", conversion="900",
                  category="200", plan="300", extras="100")
        self.assertEqual(self.post(5, "month", action="close").status_code, 303)
        closed_state, closed_rows = adm.model(self.conn, "2026-11")
        self.assertEqual(closed_state["closed"], 1)
        self.assertEqual(closed_rows[0]["total"], 318000)
        before = self.snapshot()
        self.assertEqual(self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="8").status_code, 409)
        self.assertEqual(self.post(5, "payroll", staff_id=str(sid), standards="0", conversion="0",
                                   category="0", plan="0", extras="0").status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client(1).post("/administrators/settings", data=dict(
            csrf="admin-test-csrf", month="2026-12",
            version=str(adm.month_state(self.conn, "2026-12")["version"]),
            staff_id=str(sid), full_name="Иванова Анна",
            rate="250,00", active="1",
        )).status_code, 303)
        self.assertEqual(adm.model(self.conn, "2026-11")[1][0]["total"], 318000)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["rate"], 25000)
        self.assertEqual(self.post(1, "month", action="reopen").status_code, 303)
        self.assertEqual(adm.month_state(self.conn, "2026-11")["closed"], 0)
        self.assertEqual(self.post(4, "hours", staff_id=str(sid), work_date="2026-11-04", value="8").status_code, 303)
        events = [r[0] for r in self.conn.execute("SELECT action FROM az_admin_events ORDER BY id")]
        self.assertIn("month_closed", events)
        self.assertIn("month_reopened", events)

    def test_csrf_invalid_hours_and_tamper_do_not_write(self):
        sid = self.staff()
        before = self.snapshot()
        self.assertEqual(self.client(1).post("/administrators/hours", data=dict(
            csrf="wrong", month="2026-11", staff_id=sid, work_date="2026-11-04", value="8",
        )).status_code, 400)
        for value in ("0", "-1", "25", "foo", "8.333"):
            self.assertEqual(self.post(4, "hours", staff_id=str(sid),
                                       work_date="2026-11-04", value=value).status_code, 400)
        self.assertEqual(self.post(4, "hours", staff_id=str(sid),
                                   work_date="2026-12-04", value="8").status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_retains_history_and_settings_can_reactivate(self):
        sid = self.staff()
        self.post(4, "hours", staff_id=str(sid), work_date="2026-11-03", value="10")
        response = self.post(1, "settings", staff_id=str(sid),
                             full_name="Иванова Анна", rate="200", active="0")
        self.assertEqual(response.status_code, 303)
        self.assertIn("Неактивен", self.client(1).get("/administrators/?month=2026-11").text)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["minutes"], 600)
        self.assertEqual(self.post(1, "settings", staff_id=str(sid),
                                   full_name="Иванова Анна", rate="200", active="1").status_code, 303)
        self.assertTrue(adm.read_rows(self.conn, "2026-11")[0]["active"])

    def test_schema_is_isolated_and_idempotent(self):
        self.staff()
        before=self.snapshot()
        adm.init_schema(self.conn)
        self.assertEqual(before,self.snapshot())
        tables={r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(adm.TABLES).issubset(tables))
        self.assertEqual(adm.month_shift("2026-12", 1), "2027-01")
        self.assertEqual(adm.month_shift("2027-01", -1), "2026-12")



    def test_az_252_01_stale_month_form_cannot_erase_premiums_or_close(self):
        sid = self.staff()
        stale = adm.month_state(self.conn, "2026-11")["version"]
        self.assertEqual(
            self.post(5, "payroll", staff_id=str(sid), version=stale,
                      standards="1800", conversion="0", category="0", plan="0", extras="0").status_code,
            303,
        )
        self.assertEqual(adm.month_state(self.conn, "2026-11")["version"], stale + 1)
        after_first = self.snapshot()
        self.assertEqual(
            self.post(5, "payroll", staff_id=str(sid), version=stale,
                      standards="0", conversion="0", category="500", plan="0", extras="0").status_code,
            409,
        )
        self.assertEqual(self.snapshot(), after_first)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["bonus"]["standards"], 180000)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["bonus"]["category"], 0)
        self.assertEqual(self.post(5, "month", action="close", version=stale).status_code, 409)
        self.assertEqual(self.snapshot(), after_first)
        refreshed = adm.month_state(self.conn, "2026-11")["version"]
        self.assertEqual(self.post(5, "month", action="close", version=refreshed).status_code, 303)
        closed_version = adm.month_state(self.conn, "2026-11")["version"]
        self.assertEqual(closed_version, refreshed + 1)
        self.assertEqual(self.post(1, "month", action="reopen", version=refreshed).status_code, 409)
        self.assertTrue(adm.month_state(self.conn, "2026-11")["closed"])
        self.assertEqual(self.post(1, "month", action="reopen", version=closed_version).status_code, 303)

    def test_az_252_01_old_hours_and_settings_forms_are_rejected(self):
        sid = self.staff()
        stale = adm.month_state(self.conn, "2026-11")["version"]
        self.assertEqual(
            self.post(4, "hours", staff_id=str(sid), work_date="2026-11-03",
                      value="12", version=stale).status_code, 303
        )
        saved = self.snapshot()
        self.assertEqual(
            self.post(4, "hours", staff_id=str(sid), work_date="2026-11-03",
                      value="8", version=stale).status_code, 409
        )
        self.assertEqual(
            self.post(1, "settings", staff_id=str(sid), full_name="Другое ФИО",
                      rate="250", active="1", version=stale).status_code, 409
        )
        self.assertEqual(self.snapshot(), saved)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["minutes"], 720)

    def test_az_252_02_reopen_grant_protects_account_edit_and_password_reset(self):
        self.conn.execute("INSERT INTO permissions(user_id,section) VALUES(?,?)",
                          (5, "admins_reopen"))
        self.conn.commit()
        before = self.snapshot()
        url = "/admin/users/5"
        non_owner = self.client(2)
        self.assertEqual(non_owner.get(url + "/edit").status_code, 403)
        self.assertEqual(non_owner.post(url + "/edit", data={
            "csrf": "admin-test-csrf", "full_name": "Взлом",
            "email": "attacker@example.test", "active": "1", "is_admin": "1"
        }).status_code, 403)
        reset = non_owner.post(url + "/reset-password", data={"csrf": "admin-test-csrf"})
        self.assertEqual(reset.status_code, 403)
        self.assertNotIn("Временный пароль", reset.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client(1).get(url + "/edit").status_code, 200)
        owner_reset = self.client(1).post(url + "/reset-password",
                                          data={"csrf": "admin-test-csrf"})
        self.assertEqual(owner_reset.status_code, 200)
        self.assertIn("Временный пароль", owner_reset.get_data(as_text=True))

    def test_az_252_03_past_rate_change_preserves_future_inactive_month(self):
        sid = self.staff(rate="200")
        self.assertEqual(self.post(1, "settings", month="2026-12", staff_id=str(sid),
                                   full_name="Иванова Анна", rate="200", active="0").status_code, 303)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["active"], False)
        self.assertEqual(self.post(1, "settings", month="2026-11", staff_id=str(sid),
                                   full_name="Иванова Анна", rate="210", active="1").status_code, 303)
        self.assertEqual(self.conn.execute(
            "SELECT inactive_month FROM az_admin_staff WHERE id=?", (sid,)).fetchone()[0],
            "2026-12")
        self.assertTrue(adm.read_rows(self.conn, "2026-11")[0]["active"])
        self.assertFalse(adm.read_rows(self.conn, "2026-12")[0]["active"])
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["rate"], 21000)

    def test_az_252_01_missing_or_invalid_version_fails_closed(self):
        sid = self.staff()
        before = self.snapshot()
        for version in ("", "-1", "abc", "9999999999999999999999"):
            self.assertEqual(self.post(5, "payroll", staff_id=str(sid),
                standards="1800", conversion="0", category="0", plan="0",
                extras="0", version=version).status_code, 400)
        raw = self.client(5).post("/administrators/month", data={
            "csrf": "admin-test-csrf", "month": "2026-11", "action": "close"})
        self.assertEqual(raw.status_code, 400)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
