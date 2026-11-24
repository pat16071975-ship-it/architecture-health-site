"""AZ administrators independent worktime/pay tests. No production data."""
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
        view_revision = fields.pop("view_revision", adm.month_view_revision(self.conn, month))
        data = dict(csrf="admin-test-csrf", month=month, version=str(version),
                    view_revision=str(view_revision))
        data.update(fields)
        return self.client(uid).post("/administrators/" + endpoint, data=data)

    def staff(self, rate="200,00", name="Иванова Анна"):
        response = self.post(1, "settings", full_name=name, rate=rate, active="1")
        self.assertEqual(response.status_code, 303)
        return self.conn.execute("SELECT id FROM az_admin_staff ORDER BY id DESC LIMIT 1").fetchone()["id"]

    def batch_data(self, month="2026-11", changes=None):
        """Full visible matrix with current optimistic tokens; override only chosen cells."""
        state, rows, revision = adm.month_page_snapshot(self.conn, month)
        count = __import__("calendar").monthrange(*map(int, month.split("-")))[1]
        data = dict(csrf="admin-test-csrf", month=month,
                    version=str(state["version"]), view_revision=revision)
        for row in rows:
            for day in range(1, count + 1):
                data[f"hours_{row['id']}_{day:02d}"] = row["days"].get(day, "")
        for (sid, day), value in (changes or {}).items():
            data[f"hours_{sid}_{day:02d}"] = value
        return data

    def save_batch(self, uid=4, month="2026-11", changes=None, data=None):
        return self.client(uid).post("/administrators/hours/batch",
                                     data=data if data is not None
                                     else self.batch_data(month, changes))

    def snapshot(self):
        return "\n".join(self.conn.iterdump())

    def independent_http_rate_update(self, staff_id):
        """Separate thread => separate Flask request context and DB connection."""
        month = "2026-11"
        post_data = {
            "csrf": "admin-test-csrf",
            "month": month,
            "version": str(adm.month_state(self.conn, month)["version"]),
            "view_revision": adm.month_view_revision(self.conn, month),
            "staff_id": str(staff_id),
            "full_name": "Иванова Анна",
            "rate": "210",
            "active": "1",
        }

        def write_http():
            return self.client(1).post("/administrators/settings", data=post_data).status_code

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(write_http).result(timeout=10)

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
        self.assertIn("Иванова А.", res.text)
        self.assertIn('name="hours_' + str(sid) + '_04"', res.text)
        self.assertNotIn('<option value="off"', res.text)
        self.assertNotIn('class="az-admin-grid select"', res.text)
        self.assertIn('id="az-hours-batch"', res.text)
        self.assertIn('action="/administrators/hours/batch"', res.text)
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

    def test_az_admin_grid_all_31_days_and_short_display_preserve_full_name(self):
        sid = self.staff(name="Иванова Анна Петровна")
        page = self.client(4).get("/administrators/?month=2026-12")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Иванова А. П.", page.text)
        self.assertIn('title="Иванова Анна Петровна"', page.text)
        self.assertEqual(len(re.findall(
            r'class="az-hour-input"[^>]*name="hours_' + str(sid) + r'_\d\d"',
            page.text
        )), 31)
        self.assertIn('name="hours_' + str(sid) + '_31"', page.text)
        self.assertNotIn("<select name=\"value\"", page.text)
        self.assertNotIn("min-width:max-content", page.text)
        self.assertNotIn('горизонтальной прокруткой"', page.text)
        self.assertEqual(adm.short_staff_name("Иванов"), "Иванов")
        self.assertEqual(adm.short_staff_name("Петров-Сидоров Иван Александрович"),
                         "Петров-Сидоров И. А.")
        self.assertEqual(self.conn.execute(
            "SELECT full_name FROM az_admin_staff WHERE id=?", (sid,)
        ).fetchone()[0], "Иванова Анна Петровна")
        settings = self.client(1).get("/administrators/?tab=settings&month=2026-12")
        self.assertIn('value="Иванова Анна Петровна"', settings.text)

    def test_az_admin_batch_saved_only_on_explicit_submit_and_success_proof(self):
        first = self.staff(name="Иванова Анна Петровна")
        second = self.staff(name="Петрова Мария")
        data = self.batch_data(changes={(first, 4): "8", (first, 5): "7,5",
                                        (second, 24): "9.25"})
        before = self.snapshot()
        self.assertIn('value=""', self.client(4).get(
            "/administrators/?month=2026-11").text)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn("data-az-upload-feedback", self.client(4).get(
            "/administrators/?month=2026-11&saved=1").text)
        result = self.save_batch(data=data)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.snapshot().count("INSERT INTO \"az_admin_days\""), 3)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["minutes"], 930)
        self.assertEqual(sum(r["minutes"] for r in adm.read_rows(self.conn, "2026-11")),
                         1485)
        self.assertEqual(self.conn.execute(
            "SELECT version FROM az_admin_months WHERE month='2026-11'"
        ).fetchone()[0], 3)  # two staff settings + one batch save
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM az_admin_events WHERE action='hours'"
        ).fetchone()[0], 3)
        client = self.client(4)
        # Proof is tied to the same session that saved, not another login.
        response = client.post("/administrators/hours/batch",
                               data=self.batch_data())
        self.assertEqual(response.status_code, 303)
        shown = client.get(response.location)
        self.assertIn("ТАБЕЛЬ СОХРАНЁН", shown.text)
        self.assertIn("ноябрь 2026 года", shown.text)
        self.assertNotIn("ТАБЕЛЬ СОХРАНЁН", client.get(response.location).text)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM az_admin_events WHERE action='hours'"
        ).fetchone()[0], 3)  # no-op save leaves history unchanged

    def test_az_admin_batch_blank_zero_and_fractional_hours(self):
        sid = self.staff()
        self.assertEqual(self.save_batch(changes={
            (sid, 1): "8,5", (sid, 2): "7.25",
            (sid, 3): "0", (sid, 4): ""
        }).status_code, 303)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["hours"], "15.75")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM az_admin_days")
                         .fetchone()[0], 2)
        self.assertEqual(self.save_batch(changes={(sid, 1): "", (sid, 2): "0"})
                         .status_code, 303)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM az_admin_days")
                         .fetchone()[0], 0)
        self.assertEqual(adm.read_rows(self.conn, "2026-11")[0]["hours"], "0")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM az_admin_events "
                         "WHERE action='hours'").fetchone()[0], 4)

    def test_az_admin_batch_atomic_rejects_invalid_extra_missing_or_duplicate(self):
        sid = self.staff()
        for value in ("-1", "25", "8.333", "NaN", "off", "вых", "foo"):
            with self.subTest(value=value):
                saved = self.snapshot()
                result = self.save_batch(changes={(sid, 5): value})
                self.assertEqual(result.status_code, 400)
                self.assertEqual(self.snapshot(), saved)
        base = self.batch_data(changes={(sid, 2): "8", (sid, 3): "9"})
        for mutation in ("missing", "extra", "duplicate", "csrf"):
            with self.subTest(mutation=mutation):
                supplied = dict(base)
                if mutation == "missing":
                    del supplied[f"hours_{sid}_31"]
                if mutation == "extra":
                    supplied["hours_999_04"] = "8"
                if mutation == "duplicate":
                    supplied[f"hours_{sid}_02"] = ["8", "11"]
                if mutation == "csrf":
                    supplied["csrf"] = "wrong-token"
                saved = self.snapshot()
                self.assertEqual(self.save_batch(data=supplied).status_code, 400)
                self.assertEqual(self.snapshot(), saved)

    def test_az_admin_batch_version_and_inherited_rate_guard(self):
        sid = self.staff()
        old = self.batch_data(changes={(sid, 2): "11"})
        self.assertEqual(self.post(1, "settings", staff_id=str(sid),
                                   full_name="Иванова Анна", rate="210,00",
                                   active="1").status_code, 303)
        before = self.snapshot()
        self.assertEqual(self.save_batch(data=old).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        current = self.batch_data(changes={(sid, 2): "11"})
        self.assertEqual(self.save_batch(data=current).status_code, 303)
        self.assertEqual(self.conn.execute(
            "SELECT minutes FROM az_admin_days WHERE staff_id=? "
            "AND work_date='2026-11-02'", (sid,)
        ).fetchone()[0], 660)
        self.assertEqual(self.save_batch(data=current).status_code, 409)
        self.assertEqual(self.client(3).post(
            "/administrators/hours/batch", data=self.batch_data()).status_code,403)

    def test_az_admin_batch_closed_month_and_next_month_bounds(self):
        sid = self.staff()
        dec = self.batch_data(month="2026-12", changes={(sid, 31): "12"})
        self.assertEqual(self.save_batch(month="2026-12", data=dec).status_code,303)
        self.assertIn('name="hours_' + str(sid) + '_31"',
                      self.client(4).get("/administrators/?month=2026-12").text)
        self.assertEqual(self.post(5, "month", month="2026-12",
                                   action="close").status_code, 303)
        before = self.snapshot()
        blocked = self.batch_data(month="2026-12")
        blocked["version"] = str(adm.month_state(self.conn, "2026-12")["version"])
        self.assertEqual(self.save_batch(month="2026-12", data=blocked).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        closed = self.client(4).get("/administrators/?month=2026-12").text
        self.assertNotIn('id="az-hours-batch"', closed)
        self.assertNotIn('class="az-hour-input"', closed)
        self.assertIn("12", closed)

    def test_az_admin_settings_history_readonly_all_actions_and_pagination(self):
        sid = self.staff()
        self.save_batch(changes={(sid, 4): "8", (sid, 5): "9"})
        self.post(5, "payroll", staff_id=str(sid),
                  standards="1800", conversion="900", category="200",
                  plan="300", extras="100")
        self.post(1, "settings", staff_id=str(sid),
                  full_name="Иванова Анна", rate="220", active="1")
        self.post(5, "month", action="close")
        self.post(1, "month", action="reopen")
        before = self.snapshot()
        view = self.client(3).get("/administrators/?tab=settings&month=2026-11")
        self.assertEqual(view.status_code, 200)
        for value in ("Журнал изменений", "11.2026", "Иванова А.",
                      "Ставка:", "Стандарты:", "Месяц закрыт",
                      "Месяц повторно открыт", "04 число:"):
            self.assertIn(value, view.text)
        self.assertEqual(self.snapshot(), before)
        # Read-only paginated history, no hidden mutations.
        with self.conn:
            self.conn.executemany(
                "INSERT INTO az_admin_events(actor_id,month,staff_id,action,"
                "before_json,after_json,created_at) VALUES(1,?,?,'hours',?,?,?)",
                [("2026-10", sid, '{"date":"2026-10-01","minutes":null}',
                  '{"date":"2026-10-01","minutes":60}', "2026-11-24T10:00:00+07:00")]
                * 104,
            )
        first = self.client(3).get("/administrators/?tab=settings&history_page=1")
        nextpage = self.client(3).get("/administrators/?tab=settings&history_page=2")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(nextpage.status_code, 200)
        self.assertIn("Страница 1 из 2", first.text)
        self.assertIn("Страница 2 из 2", nextpage.text)
        self.assertEqual(self.client(3).get("/administrators/?tab=settings&history_page=abc").status_code,400)

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
            view_revision=adm.month_view_revision(self.conn, "2026-12"),
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



    def test_az_252_01_cross_month_old_december_settings_cannot_restore_november_rate(self):
        """Review reproduction: December 200 RUB form, November 200→210, stale December POST."""
        sid = self.staff(rate="200")
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
        ).status_code, 303)
        dec_state = adm.month_state(self.conn, "2026-12")
        dec_version = dec_state["version"]
        stale_revision = adm.month_view_revision(self.conn, "2026-12")
        old_form = self.client(1).get("/administrators/?tab=settings&month=2026-12")
        self.assertEqual(old_form.status_code, 200)
        self.assertIn('name="view_revision" value="' + stale_revision + '"', old_form.text)
        self.assertIn('name="rate"', old_form.text)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["rate"], 20000)

        self.assertEqual(self.post(
            1, "settings", month="2026-11", staff_id=str(sid),
            full_name="Иванова Анна", rate="210", active="1",
        ).status_code, 303)
        self.assertEqual(adm.month_state(self.conn, "2026-12")["version"], dec_version)
        self.assertNotEqual(adm.month_view_revision(self.conn, "2026-12"), stale_revision)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["rate"], 21000)
        before = self.snapshot()

        stale = self.post(
            1, "settings", month="2026-12", staff_id=str(sid),
            full_name="Иванова Анна", rate="200", active="1",
            version=dec_version, view_revision=stale_revision,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["rate"], 21000)
        self.assertEqual(adm.month_state(self.conn, "2026-12")["version"], dec_version)
        refreshed = self.client(1).get("/administrators/?tab=settings&month=2026-12")
        self.assertIn('value="210,00"', refreshed.text)
        self.assertEqual(self.post(
            1, "settings", month="2026-12", staff_id=str(sid),
            full_name="Иванова Анна", rate="210", active="1",
        ).status_code, 303)

    def test_az_252_01_cross_month_stale_december_close_must_not_approve_2100_after_seeing_2000(self):
        """Review reproduction: old 2,000 RUB close form must reject after inherited rate becomes 2,100."""
        sid = self.staff(rate="200")
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
        ).status_code, 303)
        old_version = adm.month_state(self.conn, "2026-12")["version"]
        old_revision = adm.month_view_revision(self.conn, "2026-12")
        old_form = self.client(5).get("/administrators/?tab=payroll&month=2026-12")
        self.assertIn("2 000,00", old_form.text)
        self.assertIn('name="view_revision" value="' + old_revision + '"', old_form.text)

        self.assertEqual(self.post(
            1, "settings", month="2026-11", staff_id=str(sid),
            full_name="Иванова Анна", rate="210", active="1",
        ).status_code, 303)
        self.assertEqual(adm.month_state(self.conn, "2026-12")["version"], old_version)
        self.assertNotEqual(adm.month_view_revision(self.conn, "2026-12"), old_revision)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["total"], 210000)
        before = self.snapshot()

        stale_close = self.post(
            5, "month", month="2026-12", action="close",
            version=old_version, view_revision=old_revision,
        )
        self.assertEqual(stale_close.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(adm.month_state(self.conn, "2026-12")["closed"])

        fresh = self.client(5).get("/administrators/?tab=payroll&month=2026-12")
        self.assertIn("2 100,00", fresh.text)
        self.assertEqual(self.post(
            5, "month", month="2026-12", action="close",
        ).status_code, 303)
        state, rows = adm.model(self.conn, "2026-12")
        self.assertEqual(state["closed"], 1)
        self.assertEqual(rows[0]["total"], 210000)

    def test_az_252_01_cross_month_revision_guards_hours_and_payroll_too(self):
        sid = self.staff(rate="200")
        dec_version = adm.month_state(self.conn, "2026-12")["version"]
        dec_revision = adm.month_view_revision(self.conn, "2026-12")
        self.assertEqual(self.post(
            1, "settings", month="2026-11", staff_id=str(sid),
            full_name="Иванова Анна", rate="210", active="1",
        ).status_code, 303)
        before = self.snapshot()
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
            version=dec_version, view_revision=dec_revision,
        ).status_code, 409)
        self.assertEqual(self.post(
            5, "payroll", month="2026-12", staff_id=str(sid),
            standards="1800", conversion="0", category="0", plan="0", extras="0",
            version=dec_version, view_revision=dec_revision,
        ).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
        ).status_code, 303)



    def test_az_252_01_consistent_html_and_token_when_rate_changes_at_token_boundary(self):
        """HTTP concurrency: page still says 2000; POST may not close refreshed 2100."""
        sid = self.staff(rate="200")
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
        ).status_code, 303)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        old_version = adm.month_state(self.conn, "2026-12")["version"]
        old_revision = adm.month_view_revision(self.conn, "2026-12")
        original = adm.month_view_revision_from_model
        switched = []

        def concurrent_update_between_model_and_token(month, state, rows):
            if month == "2026-12" and not switched:
                self.assertEqual(rows[0]["total"], 200000)
                switched.append(True)
                # Separate authenticated HTTP request during the reader's GET.
                self.assertEqual(self.independent_http_rate_update(sid), 303)
            return original(month, state, rows)

        with patch.object(adm, "month_view_revision_from_model",
                          side_effect=concurrent_update_between_model_and_token):
            page = self.client(5).get("/administrators/?tab=payroll&month=2026-12")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(switched), 1)
        self.assertIn("2 000,00", page.text)
        match = re.search(r'name="view_revision" value="([0-9a-f]{64})"', page.text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), old_revision)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["total"], 210000)
        self.assertNotEqual(adm.month_view_revision(self.conn, "2026-12"), old_revision)
        before = self.snapshot()
        result = self.post(
            5, "month", month="2026-12", action="close",
            version=old_version, view_revision=match.group(1),
        )
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(adm.month_state(self.conn, "2026-12")["closed"])
        refreshed = self.client(5).get("/administrators/?tab=payroll&month=2026-12")
        self.assertIn("2 100,00", refreshed.text)

    def test_az_252_01_reads_page_model_on_one_sqlite_snapshot(self):
        """WAL writer commits after the first GET SELECT, before read_rows SELECTs."""
        sid = self.staff(rate="200")
        self.assertEqual(self.post(
            4, "hours", month="2026-12", staff_id=str(sid),
            work_date="2026-12-03", value="10",
        ).status_code, 303)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        old_revision = adm.month_view_revision(self.conn, "2026-12")
        old_version = adm.month_state(self.conn, "2026-12")["version"]
        original_rows = adm.read_rows
        switched = []

        def update_after_first_select(conn, month):
            if month == "2026-12" and not switched:
                switched.append(True)
                self.assertEqual(self.independent_http_rate_update(sid), 303)
            return original_rows(conn, month)

        with patch.object(adm, "read_rows", side_effect=update_after_first_select):
            page = self.client(5).get("/administrators/?tab=payroll&month=2026-12")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(switched), 1)
        self.assertIn("2 000,00", page.text)
        match = re.search(r'name="view_revision" value="([0-9a-f]{64})"', page.text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), old_revision)
        self.assertEqual(adm.read_rows(self.conn, "2026-12")[0]["total"], 210000)
        before = self.snapshot()
        self.assertEqual(self.post(
            5, "month", month="2026-12", action="close",
            version=old_version, view_revision=old_revision,
        ).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(adm.month_state(self.conn, "2026-12")["closed"])


if __name__ == "__main__":
    unittest.main()
