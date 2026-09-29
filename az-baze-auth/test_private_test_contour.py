import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parent
    / "tools"
    / "prepare_private_test_contour.py"
)
SPEC = importlib.util.spec_from_file_location(
    "prepare_private_test_contour",
    MODULE_PATH,
)
private_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(private_test)


class PrivateTestContourContractTests(unittest.TestCase):
    def test_environment_points_only_to_test_paths(self):
        env_text = private_test.render_env("secret")
        self.assertIn("AZBAZE_TEST_CONTOUR=1", env_text)
        self.assertIn("AZBAZE_DB=/var/lib/az-baze-test/auth.db", env_text)
        self.assertIn("AZBAZE_SITE_ROOT=/var/www/az-baze-test", env_text)
        self.assertIn(
            "AZBAZE_CREDENTIALS_KEY=/var/lib/az-baze-test/credentials.key",
            env_text,
        )
        self.assertIn(
            "AZ_FINREZ_DATA_PATH=/var/lib/az-baze-test/finrez-data.enc",
            env_text,
        )
        self.assertIn(
            "AZ_FINREZ_KEY_PATH=/var/lib/az-baze-test/finrez.key",
            env_text,
        )
        self.assertNotIn("/var/lib/az-baze/auth.db", env_text)
        self.assertNotIn("/var/www/az-baze.ru", env_text)

    def test_service_is_loopback_only_and_has_network_guard(self):
        unit = private_test.render_unit()
        self.assertIn("--bind 127.0.0.1:8002", unit)
        self.assertNotIn("--bind 0.0.0.0", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("IPAddressAllow=localhost", unit)
        self.assertIn("AZBAZE_TEST_CONTOUR", private_test.render_env("secret"))
        self.assertNotIn("nginx", unit.lower())

    def test_bootstrap_creates_only_private_owner_and_structure_permission(self):
        script = private_test.render_bootstrap_database_script("secret-password")
        self.assertIn(private_test.TEST_EMAIL, script)
        self.assertIn("VALUES(?,?,?,0,1,0,0,NULL,?,?)", script)
        self.assertIn('"structure_manage"', script)
        self.assertIn('clinic_name="Архитектура здоровья — TEST"', script)
        self.assertIn('timezone_name=\'UTC\'', script)
        self.assertNotIn("Зубачев", script)
        self.assertNotIn("INSERT INTO users", script.split("INSERT INTO users", 1)[1])

    def test_generated_bootstrap_executes_against_fresh_temp_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "auth.db"
            site_root = root / "site"
            site_root.mkdir()

            original_repo = private_test.TEST_REPO
            try:
                private_test.TEST_REPO = Path(__file__).resolve().parent.parent
                script = private_test.render_bootstrap_database_script(
                    "secret-password"
                )
            finally:
                private_test.TEST_REPO = original_repo

            env = dict(os.environ)
            env["AZBAZE_DB"] = str(db_path)
            env["AZBAZE_SECRET_KEY"] = "test-secret"
            env["AZBAZE_SITE_ROOT"] = str(site_root)

            completed = subprocess.run(
                [sys.executable, "-c", script],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )

            conn = sqlite3.connect(db_path)
            try:
                users = conn.execute(
                    "SELECT email,is_admin,active FROM users ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    users,
                    [(private_test.TEST_EMAIL, 0, 1)],
                )
                permissions = conn.execute(
                    """
                    SELECT p.section
                    FROM permissions p
                    JOIN users u ON u.id=p.user_id
                    WHERE u.email=?
                    ORDER BY p.section
                    """,
                    (private_test.TEST_EMAIL,),
                ).fetchall()
                self.assertEqual(permissions, [("structure_manage",)])
                clinics = conn.execute(
                    "SELECT name,timezone FROM clinics"
                ).fetchall()
                self.assertEqual(
                    clinics,
                    [("Архитектура здоровья — TEST", "UTC")],
                )
            finally:
                conn.close()

    def test_preflight_is_read_only_and_reports_no_live_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_db = root / "live.db"
            live_db.write_bytes(b"sqlite-placeholder")

            with (
                patch.object(private_test.socket, "gethostname", return_value="az-server"),
                patch.object(private_test, "LIVE_DB", live_db),
                patch.object(private_test, "service_active", side_effect=lambda name: name == "az-baze-auth"),
                patch.object(private_test, "port_is_free", return_value=True),
                patch.object(private_test.shutil, "which", return_value="/usr/bin/tool"),
                patch.object(private_test.pwd, "getpwnam"),
                patch.object(private_test, "test_paths", return_value=()),
            ):
                info = private_test.preflight(
                    "a" * 40,
                    require_clean=True,
                )

            self.assertFalse(info["live_db_write"])
            self.assertFalse(info["live_deploy"])
            self.assertFalse(info["nginx_change"])
            self.assertFalse(info["firewall_change"])
            self.assertEqual(info["test_bind"], "127.0.0.1:8002")

    def test_prepare_requires_exact_confirmation(self):
        with self.assertRaises(private_test.PrivateTestError):
            private_test.main(
                [
                    "--prepare",
                    "--source-commit",
                    "a" * 40,
                    "--confirm",
                    "WRONG",
                ]
            )


if __name__ == "__main__":
    unittest.main()
