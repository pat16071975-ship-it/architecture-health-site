import json
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

TEST_TMP = tempfile.TemporaryDirectory()
os.environ["AZBAZE_DB"] = str(Path(TEST_TMP.name) / "app.db")
os.environ["AZBAZE_SECRET_KEY"] = "test-secret-key-for-surveys-only"
os.environ["AZBAZE_SITE_ROOT"] = str(Path(TEST_TMP.name) / "site")

import app as app_core
import surveys

surveys.register_surveys(app_core.app)


class SurveyCalculationTests(unittest.TestCase):
    def test_scale_stats_ignore_no_observation(self):
        stats = surveys.calculate_scale_stats([5, 4, 3, 5], 2)
        self.assertEqual(stats["count"], 4)
        self.assertEqual(stats["mean"], 4.25)
        self.assertEqual(stats["median"], 4.5)
        self.assertEqual(stats["distribution"], {"1": 0, "2": 0, "3": 1, "4": 1, "5": 2})
        self.assertEqual(stats["no_observation"], 2)
        self.assertAlmostEqual(stats["no_observation_ratio"], 2 / 6)

    def test_manager_template_has_exact_title_and_25_questions_without_fixed_two_weeks(self):
        template = surveys.manager_template_form()
        self.assertEqual(template["title"], "Оценка управляющей")
        self.assertEqual(template["category"], "Административные")
        self.assertEqual(len(template["questions"]), 25)
        text = " ".join(
            [template["title"], template["description"]]
            + [item["text"] for item in template["questions"]]
        ).lower()
        self.assertNotIn("2 недели", text)
        self.assertNotIn("две недели", text)
        self.assertTrue(all(item["type"] == "scale" for item in template["questions"][:21]))
        self.assertTrue(all(item["type"] == "text" for item in template["questions"][21:]))


class SurveyPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_core.init_db_file()
        surveys.init_surveys_schema()

    def setUp(self):
        site_root = Path(os.environ["AZBAZE_SITE_ROOT"])
        site_root.mkdir(parents=True, exist_ok=True)
        (site_root / "index.html").write_text(
            """<!doctype html><html><body>
<a class="menu-btn active" href="../reports/">Отчёты</a>
<a class="menu-btn active" href="#knowledge" id="knowledge">База знаний</a>
<div class="menu-btn placeholder" data-section="section4" aria-disabled="true" hidden>Дни рождения сотрудников</div>
<div class="menu-btn placeholder" data-section="section5" aria-disabled="true" hidden>Раздел в разработке</div>
</body></html>""",
            encoding="utf-8",
        )
        with app_core.app.app_context():
            conn = app_core.db()
            for table in (
                "survey_answers", "survey_responses", "survey_invites",
                "survey_options", "survey_questions", "survey_sections", "surveys"
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM permissions")
            conn.execute("DELETE FROM users")
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(1,'admin@example.test','Admin','x',1,1,0,0,NULL,'now','now')
                """
            )
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(2,'user@example.test','User','x',0,1,0,0,NULL,'now','now')
                """
            )
            conn.commit()

    def test_admin_does_not_get_survey_capabilities_implicitly(self):
        with app_core.app.app_context():
            admin = app_core.db().execute("SELECT * FROM users WHERE id=1").fetchone()
            self.assertTrue(admin["is_admin"])
            permissions = app_core.user_permissions(admin)
            self.assertNotIn("surveys", permissions)
            self.assertNotIn("surveys_create", permissions)
            self.assertNotIn("surveys_view", permissions)
            self.assertIn("reports", permissions)

    def test_create_and_view_capabilities_are_independent_and_open_section(self):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(1,'surveys_create')")
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(2,'surveys_view')")
            conn.commit()
            admin = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
            user = conn.execute("SELECT * FROM users WHERE id=2").fetchone()

            admin_permissions = app_core.user_permissions(admin)
            self.assertIn("surveys", admin_permissions)
            self.assertIn("surveys_create", admin_permissions)
            self.assertNotIn("surveys_view", admin_permissions)

            user_permissions = app_core.user_permissions(user)
            self.assertIn("surveys", user_permissions)
            self.assertIn("surveys_view", user_permissions)
            self.assertNotIn("surveys_create", user_permissions)

    def test_direct_surveys_url_is_403_without_explicit_permission(self):
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 2
            session["csrf"] = "test"
        response = client.get("/surveys/")
        self.assertEqual(response.status_code, 403)

    def test_admin_direct_surveys_url_is_also_403_without_explicit_permission(self):
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["csrf"] = "test"
        response = client.get("/surveys/")
        self.assertEqual(response.status_code, 403)

    def test_home_injects_surveys_after_normalizing_static_links(self):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(2,'surveys_view')")
            conn.commit()
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 2
            session["csrf"] = "test"
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/surveys/"', html)
        self.assertIn("Опросы", html)
        self.assertNotIn('href="/reports/"', html)
        self.assertNotIn('href="/knowledge/"', html)

    def test_home_does_not_show_surveys_to_admin_without_explicit_survey_rights(self):
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["csrf"] = "test"
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('href="/surveys/"', html)
        self.assertIn('href="/reports/"', html)
        self.assertIn('href="/knowledge/"', html)

    def test_create_only_can_create_but_cannot_view_results(self):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(2,'surveys_create')")
            cur = conn.execute(
                """
                INSERT INTO surveys(
                    title,category,description,period_label,starts_at,ends_at,
                    expected_responses,status,created_by_user_id,created_at,closed_at
                ) VALUES('Closed','Административные','','',NULL,NULL,1,'CLOSED',2,'now','now')
                """
            )
            survey_id = cur.lastrowid
            conn.commit()
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 2
            session["csrf"] = "test"
        self.assertEqual(client.get("/surveys/").status_code, 200)
        self.assertEqual(client.get("/surveys/new").status_code, 200)
        self.assertEqual(client.get(f"/surveys/{survey_id}/results").status_code, 403)

    def test_view_only_can_view_closed_results_but_cannot_create(self):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(2,'surveys_view')")
            cur = conn.execute(
                """
                INSERT INTO surveys(
                    title,category,description,period_label,starts_at,ends_at,
                    expected_responses,status,created_by_user_id,created_at,closed_at
                ) VALUES('Closed','Административные','','',NULL,NULL,1,'CLOSED',2,'now','now')
                """
            )
            survey_id = cur.lastrowid
            conn.commit()
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 2
            session["csrf"] = "test"
        self.assertEqual(client.get("/surveys/").status_code, 200)
        self.assertEqual(client.get("/surveys/new").status_code, 403)
        self.assertEqual(client.get(f"/surveys/{survey_id}/results").status_code, 200)


class SurveyPublicRouteTests(unittest.TestCase):
    def setUp(self):
        app_core.init_db_file()
        surveys.init_surveys_schema()
        with app_core.app.app_context():
            conn = app_core.db()
            for table in (
                "survey_answers", "survey_responses", "survey_invites",
                "survey_options", "survey_questions", "survey_sections", "surveys"
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM permissions")
            conn.execute("DELETE FROM users")
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(1,'owner@example.test','Owner','x',0,1,0,0,NULL,'now','now')
                """
            )
            cur = conn.execute(
                """
                INSERT INTO surveys(
                    title,category,description,period_label,starts_at,ends_at,
                    expected_responses,status,created_by_user_id,created_at,closed_at
                ) VALUES('Тест','Административные','','Сентябрь 2026',NULL,NULL,1,'OPEN',1,'now',NULL)
                """
            )
            self.survey_id = cur.lastrowid
            qcur = conn.execute(
                """
                INSERT INTO survey_questions(
                    survey_id,section_id,question_type,text,required,sort_order
                ) VALUES(?,NULL,'scale','Тестовый вопрос',1,1)
                """,
                (self.survey_id,),
            )
            self.question_id = qcur.lastrowid
            icur = conn.execute(
                "INSERT INTO survey_invites(survey_id,token_hash,used) VALUES(?,?,0)",
                (self.survey_id, "placeholder"),
            )
            invite_id = icur.lastrowid
            self.token = surveys.derive_invite_token(app_core.app.config["SECRET_KEY"], invite_id)
            conn.execute(
                "UPDATE survey_invites SET token_hash=? WHERE id=?",
                (surveys.token_hash(self.token), invite_id),
            )
            conn.commit()

    def test_anonymous_shell_has_privacy_headers_no_external_fonts_and_no_token_path(self):
        client = app_core.app.test_client()
        response = client.get("/survey/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers.get("Content-Security-Policy", ""))
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn(b"fonts.googleapis", response.data)
        self.assertNotIn(self.token.encode("ascii"), response.data)
        self.assertFalse(any("<token>" in rule.rule for rule in app_core.app.url_map.iter_rules()))

    def test_public_submit_button_does_not_overlay_questions(self):
        client = app_core.app.test_client()
        response = client.get("/survey/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(".submit-wrap{margin-top:18px;padding:12px 0}", html)
        self.assertNotIn(".submit-wrap{position:sticky", html)
        self.assertNotIn(".submit-wrap{position:fixed", html)

    def test_public_load_and_submit_are_one_time_without_login(self):
        client = app_core.app.test_client()
        loaded = client.post(
            "/survey/",
            data={"action": "load", "token": self.token},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(loaded.status_code, 200)
        payload = loaded.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["survey"]["title"], "Тест")
        self.assertEqual(payload["survey"]["period"], "Сентябрь 2026")

        response = client.post(
            "/survey/",
            data={"action": "submit", "token": self.token, f"q_{self.question_id}": "5"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        second = client.post(
            "/survey/",
            data={"action": "submit", "token": self.token, f"q_{self.question_id}": "4"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(second.status_code, 410)
        with app_core.app.app_context():
            conn = app_core.db()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM survey_answers").fetchone()[0], 1)


class SurveyLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        app_core.init_db_file()
        surveys.init_surveys_schema()
        with app_core.app.app_context():
            conn = app_core.db()
            for table in (
                "survey_answers", "survey_responses", "survey_invites",
                "survey_options", "survey_questions", "survey_sections", "surveys"
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM permissions")
            conn.execute("DELETE FROM users")
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(1,'lead@example.test','Lead','x',0,1,0,0,NULL,'now','now')
                """
            )
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(1,'surveys_create')")
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(1,'surveys_view')")
            conn.commit()
        self.client = app_core.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["csrf"] = "survey-test-csrf"

    def test_full_draft_open_answer_close_results_export_clone_flow(self):
        questions = [
            {
                "section": "Оценка",
                "text": "Работа организована понятно.",
                "type": "scale",
                "required": True,
                "options": [],
            },
            {
                "section": "Комментарии",
                "text": "Что ещё важно?",
                "type": "text",
                "required": False,
                "options": [],
            },
        ]
        created = self.client.post(
            "/surveys/new",
            data={
                "csrf": "survey-test-csrf",
                "title": "Интеграционный тест",
                "category": "Административные",
                "description": "Тестовый запуск",
                "period_label": "Сентябрь 2026",
                "starts_at": "",
                "ends_at": "",
                "expected_responses": "4",
                "questions_json": json.dumps(questions, ensure_ascii=False),
            },
        )
        self.assertEqual(created.status_code, 302)
        match = re.search(r"/surveys/(\d+)/", created.headers["Location"])
        self.assertIsNotNone(match)
        survey_id = int(match.group(1))

        opened = self.client.post(
            f"/surveys/{survey_id}/open",
            data={"csrf": "survey-test-csrf"},
        )
        self.assertEqual(opened.status_code, 302)

        # OPEN survey structure is immutable and results stay closed.
        self.assertEqual(self.client.get(f"/surveys/{survey_id}/edit").status_code, 409)
        self.assertEqual(self.client.get(f"/surveys/{survey_id}/results").status_code, 403)

        with app_core.app.app_context():
            conn = app_core.db()
            invites = conn.execute(
                "SELECT id FROM survey_invites WHERE survey_id=? ORDER BY id",
                (survey_id,),
            ).fetchall()
            self.assertEqual(len(invites), 4)
            qrows = conn.execute(
                "SELECT id,question_type FROM survey_questions WHERE survey_id=? ORDER BY sort_order",
                (survey_id,),
            ).fetchall()
            scale_id = qrows[0]["id"]
            text_id = qrows[1]["id"]
            tokens = [
                surveys.derive_invite_token(app_core.app.config["SECRET_KEY"], row["id"])
                for row in invites
            ]

        # Secret stays out of the HTTP request URL; it is POST body data only.
        for index, (token, score) in enumerate(zip(tokens[:3], ("5", "3", "1"))):
            loaded = self.client.post(
                "/survey/",
                data={"action": "load", "token": token},
                headers={"Accept": "application/json"},
            )
            self.assertEqual(loaded.status_code, 200)
            self.assertTrue(loaded.get_json()["ok"])
            payload = {
                "action": "submit",
                "token": token,
                f"q_{scale_id}": score,
            }
            if index == 0:
                payload[f"q_{text_id}"] = "<script>alert(1)</script>"
            submitted = self.client.post(
                "/survey/",
                data=payload,
                headers={"Accept": "application/json"},
            )
            self.assertEqual(submitted.status_code, 200)
            self.assertTrue(submitted.get_json()["ok"])

        detail = self.client.get(f"/surveys/{survey_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("3 / 4".encode("utf-8"), detail.data)

        closed = self.client.post(
            f"/surveys/{survey_id}/close",
            data={"csrf": "survey-test-csrf"},
        )
        self.assertEqual(closed.status_code, 302)

        # An unused fourth invite cannot be submitted after CLOSED.
        blocked = self.client.post(
            "/survey/",
            data={
                "action": "submit",
                "token": tokens[3],
                f"q_{scale_id}": "5",
            },
            headers={"Accept": "application/json"},
        )
        self.assertEqual(blocked.status_code, 410)

        results = self.client.get(f"/surveys/{survey_id}/results")
        self.assertEqual(results.status_code, 200)
        self.assertIn(b"3.0", results.data)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", results.data)
        self.assertNotIn(b"<script>alert(1)</script>", results.data)

        exported_json = self.client.get(f"/surveys/{survey_id}/export.json")
        self.assertEqual(exported_json.status_code, 200)
        json_text = exported_json.get_data(as_text=True)
        for forbidden in tokens + ["token_hash", "invite_id", "response_id", "user_id"]:
            self.assertNotIn(forbidden, json_text)

        exported_csv = self.client.get(f"/surveys/{survey_id}/export.csv")
        self.assertEqual(exported_csv.status_code, 200)
        csv_text = exported_csv.get_data(as_text=True)
        for forbidden in tokens + ["token_hash", "invite_id", "response_id", "user_id"]:
            self.assertNotIn(forbidden, csv_text)

        cloned = self.client.post(
            f"/surveys/{survey_id}/clone",
            data={"csrf": "survey-test-csrf"},
        )
        self.assertEqual(cloned.status_code, 302)
        clone_match = re.search(r"/surveys/(\d+)/edit", cloned.headers["Location"])
        self.assertIsNotNone(clone_match)
        clone_id = int(clone_match.group(1))
        self.assertNotEqual(clone_id, survey_id)

        with app_core.app.app_context():
            conn = app_core.db()
            clone = conn.execute("SELECT status,period_label FROM surveys WHERE id=?", (clone_id,)).fetchone()
            self.assertEqual(clone["status"], "DRAFT")
            self.assertEqual(clone["period_label"], "")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM survey_invites WHERE survey_id=?", (clone_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM survey_responses WHERE survey_id=?", (clone_id,)).fetchone()[0],
                0,
            )


class SurveyAtomicResponseTests(unittest.TestCase):
    def make_db(self):
        temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp.close()
        path = temp.name
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO users(id) VALUES(1)")
        conn.executescript(surveys.SURVEY_SCHEMA)
        cur = conn.execute(
            """
            INSERT INTO surveys(
                title,category,description,period_label,starts_at,ends_at,
                expected_responses,status,created_by_user_id,created_at,closed_at
            ) VALUES('Test','Административные','','',NULL,NULL,1,'OPEN',1,'now',NULL)
            """
        )
        survey_id = cur.lastrowid
        qcur = conn.execute(
            """
            INSERT INTO survey_questions(
                survey_id,section_id,question_type,text,required,sort_order
            ) VALUES(?,NULL,'scale','Q',1,1)
            """,
            (survey_id,),
        )
        question_id = qcur.lastrowid
        digest = surveys.token_hash("test-token")
        conn.execute(
            "INSERT INTO survey_invites(survey_id,token_hash,used) VALUES(?,?,0)",
            (survey_id, digest),
        )
        conn.commit()
        conn.close()
        return path, survey_id, question_id, digest

    def test_response_schema_has_no_invite_or_token_link(self):
        path, _survey_id, _question_id, _digest = self.make_db()
        try:
            conn = sqlite3.connect(path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(survey_responses)").fetchall()}
            self.assertEqual(columns, {"id", "survey_id"})
            answer_columns = {row[1] for row in conn.execute("PRAGMA table_info(survey_answers)").fetchall()}
            self.assertFalse({"token_id", "token_hash", "user_id"} & answer_columns)
            conn.close()
        finally:
            os.unlink(path)

    def test_one_time_token_creates_only_one_response(self):
        path, survey_id, question_id, digest = self.make_db()
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self.assertTrue(surveys.record_response(conn, survey_id, digest, [(question_id, 5.0, None, None)]))
            self.assertFalse(surveys.record_response(conn, survey_id, digest, [(question_id, 4.0, None, None)]))
            count = conn.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(conn.execute("SELECT used FROM survey_invites").fetchone()[0], 1)
            conn.close()
        finally:
            os.unlink(path)

    def test_parallel_posts_still_create_one_response(self):
        path, survey_id, question_id, digest = self.make_db()
        try:
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def worker(score):
                try:
                    conn = sqlite3.connect(path, timeout=8, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA foreign_keys=ON")
                    barrier.wait()
                    results.append(
                        surveys.record_response(
                            conn,
                            survey_id,
                            digest,
                            [(question_id, float(score), None, None)],
                        )
                    )
                    conn.close()
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(5,)), threading.Thread(target=worker, args=(4,))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=12)

            self.assertFalse(errors, errors)
            self.assertEqual(sorted(results), [False, True])
            conn = sqlite3.connect(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM survey_answers").fetchone()[0], 1)
            conn.close()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
