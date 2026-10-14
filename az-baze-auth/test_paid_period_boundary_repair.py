import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import paid_services
from tools import repair_paid_period_boundaries_2026 as repair


def compact_snapshot(as_of, paid, event_dates):
    payload = {
        "version": 2,
        "month": as_of[:7],
        "as_of_date": as_of,
        "totals": {"opening": 0, "billed": paid, "paid": paid, "closing": 0},
        "providers": {
            "Назыров У. Ж.": {"opening": 0, "billed": paid, "paid": paid, "closing": 0},
        },
        "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
        "service_items": [],
        "visit_links": {date + "|token": ["Назыров У. Ж."] for date in event_dates},
        "invoices": [
            {"staff": "Назыров У. Ж.", "invoice": str(index + 1), "date": date}
            for index, date in enumerate(event_dates)
        ],
    }
    return payload


class PaidPeriodBoundaryRepairTests(unittest.TestCase):
    def make_db(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "auth.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        paid_services.init_schema(conn)
        conn.commit()
        self.addCleanup(conn.close)
        self.addCleanup(tmp.cleanup)
        return conn, path, Path(tmp.name) / "backups"

    def insert_snapshot(self, conn, as_of, paid, event_dates, filename):
        payload = compact_snapshot(as_of, paid, event_dates)
        conn.execute(
            """
            INSERT INTO service_payment_snapshots(
                as_of_date,month,payload_json,source_filename,source_sha256,
                imported_by,imported_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                as_of, as_of[:7], json.dumps(payload, ensure_ascii=False),
                filename, "sha-" + as_of, 1, "2026-10-14T00:00:00",
            ),
        )
        conn.commit()

    def test_october_increment_is_detected_not_as_month_snapshot(self):
        conn, _path, _backup = self.make_db()
        self.insert_snapshot(
            conn, "2026-10-11", 1055490,
            ["2026-10-01", "2026-10-11"], "oct-01-11.xlsx",
        )
        self.insert_snapshot(
            conn, "2026-10-13", 92610,
            ["2026-10-12", "2026-10-13"], "oct-12-13.xlsx",
        )
        plan = repair.analyze_snapshots(conn)
        self.assertEqual([row["status"] for row in plan], ["keep_snapshot", "convert_incremental"])
        self.assertEqual(plan[1]["event_start"], "2026-10-12")

    def test_cumulative_later_snapshot_is_not_converted(self):
        conn, _path, _backup = self.make_db()
        self.insert_snapshot(conn, "2026-09-15", 500, ["2026-09-01", "2026-09-15"], "a.xlsx")
        self.insert_snapshot(conn, "2026-09-30", 900, ["2026-09-01", "2026-09-30"], "b.xlsx")
        plan = repair.analyze_snapshots(conn)
        self.assertEqual([row["status"] for row in plan], ["keep_snapshot", "keep_snapshot"])

    def test_first_partial_snapshot_is_reported_but_not_guessed(self):
        conn, _path, _backup = self.make_db()
        self.insert_snapshot(conn, "2026-01-31", 100, ["2026-01-05", "2026-01-31"], "jan.xlsx")
        plan = repair.analyze_snapshots(conn)
        self.assertEqual(plan[0]["status"], "ambiguous_first_partial")

    def test_apply_moves_only_incremental_snapshot_and_archives_original(self):
        conn, path, backup_dir = self.make_db()
        self.insert_snapshot(
            conn, "2026-10-11", 1055490,
            ["2026-10-01", "2026-10-11"], "oct-01-11.xlsx",
        )
        self.insert_snapshot(
            conn, "2026-10-13", 92610,
            ["2026-10-12", "2026-10-13"], "oct-12-13.xlsx",
        )
        plan = repair.analyze_snapshots(conn)
        periods = repair.analyze_periods(conn)
        backup, converted = repair.apply_repairs(conn, plan, periods, backup_dir)
        self.assertTrue(Path(backup).exists())
        self.assertEqual(converted, [("2026-10-12", "2026-10-13", "oct-12-13.xlsx")])
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM service_payment_snapshots WHERE as_of_date='2026-10-11'"
        ).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM service_payment_snapshots WHERE as_of_date='2026-10-13'"
        ).fetchone())
        period = conn.execute(
            """
            SELECT payload_json FROM service_payment_periods
            WHERE period_start='2026-10-12' AND period_end='2026-10-13'
            """
        ).fetchone()
        payload = json.loads(period[0])
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["providers"]["Назыров У. Ж."]["paid"], 92610)
        self.assertEqual(
            conn.execute(
                "SELECT decision FROM service_payment_versions WHERE as_of_date='2026-10-13'"
            ).fetchone()[0],
            repair.DECISION,
        )
        self.assertGreater(path.stat().st_size, 0)

    def test_saved_periods_are_included_in_year_preflight(self):
        conn, _path, _backup = self.make_db()
        payload = {
            "version": 3,
            "period_start": "2026-01-05",
            "period_end": "2026-06-30",
            "totals": {"opening": 0, "billed": 100, "paid": 100, "closing": 0},
            "providers": {
                "Назыров У. Ж.": {"opening": 0, "billed": 100, "paid": 100, "closing": 0},
            },
            "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
            "service_items": [],
            "visit_links": {
                "2026-01-05|a": ["Назыров У. Ж."],
                "2026-06-30|b": ["Назыров У. Ж."],
            },
            "invoices": [],
        }
        conn.execute(
            """
            INSERT INTO service_payment_periods(
                period_start,period_end,payload_json,source_filename,source_sha256,
                imported_by,imported_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "2026-01-05", "2026-06-30",
                json.dumps(payload, ensure_ascii=False),
                "jan-jun.xlsx", "sha-period", 1, "2026-10-13T00:00:00",
            ),
        )
        conn.commit()
        periods = repair.analyze_periods(conn)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["status"], "keep_period")
        self.assertEqual(periods[0]["event_start"], "2026-01-05")
        self.assertEqual(periods[0]["event_end"], "2026-06-30")

    def test_gap_is_fail_closed_for_apply(self):
        conn, _path, backup_dir = self.make_db()
        self.insert_snapshot(conn, "2026-08-10", 100, ["2026-08-01", "2026-08-10"], "a.xlsx")
        self.insert_snapshot(conn, "2026-08-20", 200, ["2026-08-15", "2026-08-20"], "b.xlsx")
        plan = repair.analyze_snapshots(conn)
        self.assertEqual(plan[1]["status"], "ambiguous_gap")
        with self.assertRaisesRegex(RuntimeError, "unsafe rows"):
            repair.apply_repairs(conn, plan, repair.analyze_periods(conn), backup_dir)


if __name__ == "__main__":
    unittest.main()
