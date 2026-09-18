import os
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
        with app_core.app.app_context():
            conn = app_core.db()
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

    def test_admin_does_not_get_surveys_implicitly(self):
        with app_core.app.app_context():
            admin = app_core.db().execute("SELECT * FROM users WHERE id=1").fetchone()
            self.assertTrue(admin["is_admin"])
            self.assertNotIn("surveys", app_core.user_permissions(admin))
            self.assertIn("reports", app_core.user_permissions(admin))

    def test_surveys_permission_is_explicit_for_admin_and_regular_user(self):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(1,'surveys')")
            conn.execute("INSERT INTO permissions(user_id,section) VALUES(2,'surveys')")
            conn.commit()
            admin = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
            user = conn.execute("SELECT * FROM users WHERE id=2").fetchone()
            self.assertIn("surveys", app_core.user_permissions(admin))
            self.assertIn("surveys", app_core.user_permissions(user))


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
