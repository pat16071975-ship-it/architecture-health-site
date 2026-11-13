"""AZ-203: prepared CLI shares sync core; no timer or live HTTP here."""
import io
import os
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

# Flask requires a signing key even for isolated no-network unit tests.
os.environ.setdefault("AZBAZE_SECRET_KEY", "AZ203_ONLY_UNIT_TEST_KEY_NOT_FOR_PRODUCTION")

import advertising_hourly
import metrika_auto


class HourlySyncPreparedTests(unittest.TestCase):
    def test_hourly_systemd_timer_is_prepared_but_not_enabled(self):
        root = Path(__file__).resolve().parent / "deploy"
        service = (root / "az-baze-advertising-hourly.service").read_text(encoding="utf-8")
        timer = (root / "az-baze-advertising-hourly.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=hourly", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("User=azadmin", service)
        self.assertIn("EnvironmentFile=/etc/az-baze/auth.env", service)
        self.assertIn("advertising_hourly.py", service)
        self.assertNotIn("systemctl enable", service + timer)
        self.assertNotIn("/api/reports/advertising/metrika/sync", service + timer)

    def test_timer_activation_does_not_start_service_early(self):
        # AZ-203-03: activating the timer between hourly boundaries must
        # not pull in the service. Only the [Timer] Unit= directive may
        # select it for the next OnCalendar firing.
        timer = (
            Path(__file__).resolve().parent
            / "deploy" / "az-baze-advertising-hourly.timer"
        ).read_text(encoding="utf-8")
        sections = {}
        section = None
        for raw in timer.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line
                sections.setdefault(section, {})
                continue
            self.assertIsNotNone(section)
            key, sep, value = line.partition("=")
            self.assertEqual(sep, "=")
            sections[section][key.strip()] = value.strip()

        unit = sections["[Unit]"]
        for dependency in ("Requires", "Wants", "BindsTo", "Requisite"):
            self.assertNotIn(dependency, unit)
        timing = sections["[Timer]"]
        self.assertEqual(timing["Unit"], "az-baze-advertising-hourly.service")
        self.assertEqual(timing["OnCalendar"], "hourly")
        self.assertEqual(timing["Persistent"], "false")

    def test_no_token_exits_before_database_or_network(self):
        err = io.StringIO()
        with patch.object(advertising_hourly.metrika_auto, "token_from_env", return_value=""), \
             patch.object(advertising_hourly, "db") as db_mock, \
             redirect_stderr(err):
            status = advertising_hourly.main()
        self.assertEqual(status, 2)
        self.assertIn("missing_yandex_token", err.getvalue())
        db_mock.assert_not_called()

    def test_success_uses_exact_shared_core_and_no_extra_http_path(self):
        result = {
            "changed": False,
            "audit_details": "",
            "payload": {
                "direct_rows": 28,
                "direct_period": {"from":"2026-10-27","to":"2026-11-13"},
                "direct_reused": True,
                "completed_days": [],
                "current_updated": False,
            },
        }
        output = io.StringIO()
        connection = object()
        with patch.object(advertising_hourly.metrika_auto, "token_from_env", return_value="TOKEN_NOT_FOR_OUTPUT"), \
             patch.object(advertising_hourly, "db", return_value=connection), \
             patch.object(advertising_hourly.advertising_report, "run_metrika_direct_sync", return_value=result) as sync, \
             patch.object(advertising_hourly, "audit") as audit, \
             redirect_stdout(output):
            status = advertising_hourly.main()
        self.assertEqual(status, 0)
        sync.assert_called_once_with(connection, "TOKEN_NOT_FOR_OUTPUT", actor_user_id=None)
        audit.assert_not_called()
        self.assertIn('"direct_rows": 28', output.getvalue())
        self.assertNotIn("TOKEN_NOT_FOR_OUTPUT", output.getvalue())

    def test_concurrent_manual_refresh_is_skipped(self):
        output = io.StringIO()
        with patch.object(advertising_hourly.metrika_auto, "token_from_env", return_value="TOKEN"), \
             patch.object(advertising_hourly, "db"), \
             patch.object(advertising_hourly.advertising_report, "run_metrika_direct_sync",
                          side_effect=metrika_auto.MetrikaBusyError("busy")), \
             redirect_stdout(output):
            status = advertising_hourly.main()
        self.assertEqual(status, 0)
        self.assertIn("SKIPPED", output.getvalue())

    def test_failed_sync_rolls_back_without_logging_token(self):
        err = io.StringIO()
        with patch.object(advertising_hourly.metrika_auto, "token_from_env", return_value="TOKEN_NOT_FOR_OUTPUT"), \
             patch.object(advertising_hourly, "db") as db_mock, \
             patch.object(advertising_hourly.advertising_report, "run_metrika_direct_sync",
                          side_effect=advertising_hourly.advertising_report.direct_auto.DirectApiError(
                              "Директ: HTTP 500; RequestId=123")) as sync, \
             patch.object(advertising_hourly, "audit") as audit, \
             redirect_stderr(err):
            status = advertising_hourly.main()
        self.assertEqual(status, 1)
        self.assertIn("HTTP 500", err.getvalue())
        self.assertNotIn("TOKEN_NOT_FOR_OUTPUT", err.getvalue())
        db_mock.return_value.rollback.assert_called_once()
        audit.assert_not_called()


    def test_generic_failure_does_not_echo_untrusted_exception_content(self):
        err = io.StringIO()
        with patch.object(advertising_hourly.metrika_auto, "token_from_env", return_value="SECRET_TOKEN"), \
             patch.object(advertising_hourly, "db"), \
             patch.object(advertising_hourly.advertising_report, "run_metrika_direct_sync",
                          side_effect=ValueError("UNTRUSTED_BODY_SECRET_TOKEN")), \
             redirect_stderr(err):
            status = advertising_hourly.main()
        self.assertEqual(status, 1)
        self.assertIn("ValueError", err.getvalue())
        self.assertNotIn("SECRET_TOKEN", err.getvalue())
        self.assertNotIn("UNTRUSTED_BODY", err.getvalue())


if __name__ == "__main__":
    unittest.main()
