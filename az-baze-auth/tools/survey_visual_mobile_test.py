#!/usr/bin/env python3
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(os.environ.get("AZ_SURVEY_TEST_APP_ROOT", "/opt/az-baze-auth"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if not os.environ.get("AZBAZE_SECRET_KEY"):
    raise SystemExit("PRECHECK FAIL: AZBAZE_SECRET_KEY is missing.")

import app as app_core
import server
import surveys

app = server.app
OWNER_EMAIL = "balda@inbox.ru"
TITLE_PREFIX = "AZ VISUAL TEST — "
PUBLIC_BASE = "https://az-baze.ru/survey/#"


def db():
    return app_core.db()


def require_owner():
    row = db().execute(
        "SELECT id,active FROM users WHERE email=? COLLATE NOCASE",
        (OWNER_EMAIL,),
    ).fetchone()
    if not row or int(row["active"]) != 1:
        raise SystemExit("OWNER PRECHECK FAIL")
    rights = {
        item["section"]
        for item in db().execute(
            "SELECT section FROM permissions WHERE user_id=?",
            (row["id"],),
        ).fetchall()
    }
    if not {"surveys_create", "surveys_view"} <= rights:
        raise SystemExit("OWNER RIGHTS PRECHECK FAIL")
    return int(row["id"])


def create_visual_test():
    with app.app_context():
        owner_id = require_owner()
        old = db().execute(
            "SELECT id,title FROM surveys WHERE title LIKE ?",
            (TITLE_PREFIX + "%",),
        ).fetchall()
        if old:
            ids = ",".join(str(row["id"]) for row in old)
            raise SystemExit(f"OLD VISUAL TEST EXISTS: {ids}")

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "title": TITLE_PREFIX + stamp,
            "category": "Административные",
            "description": (
                "Временный опрос только для визуальной проверки формы на телефоне. "
                "Ответы не используются и после проверки опрос будет удалён."
            ),
            "period_label": "Визуальная проверка",
            "starts_at": "",
            "ends_at": "",
            "expected_responses": 1,
            "questions": [
                {
                    "section": "ШКАЛА",
                    "text": "Тест отображения шкалы: насколько удобно читать этот вопрос на экране телефона?",
                    "type": "scale",
                    "required": True,
                    "options": [],
                },
                {
                    "section": "ОДИН ВАРИАНТ",
                    "text": "Тест отображения вопроса с одним вариантом ответа.",
                    "type": "single",
                    "required": True,
                    "options": ["Вариант А", "Вариант Б", "Вариант В"],
                },
                {
                    "section": "НЕСКОЛЬКО ВАРИАНТОВ",
                    "text": "Тест отображения вопроса с несколькими вариантами ответа.",
                    "type": "multi",
                    "required": False,
                    "options": ["Первый вариант", "Второй вариант", "Третий вариант"],
                },
                {
                    "section": "КОММЕНТАРИЙ",
                    "text": "Тест отображения свободного комментария.",
                    "type": "text",
                    "required": False,
                    "options": [],
                },
            ],
        }

        conn = db()
        survey_id = surveys._create_survey(conn, payload, owner_id)
        invite_ids = surveys._create_invites(conn, survey_id, 1)
        conn.execute(
            "UPDATE surveys SET status='OPEN' WHERE id=?",
            (survey_id,),
        )
        now = app_core.iso_now()
        conn.execute(
            """
            INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
            VALUES(?,?,?,?,?)
            """,
            (owner_id, "survey_created", owner_id, f"survey_id={survey_id};visual_test=1", now),
        )
        conn.execute(
            """
            INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
            VALUES(?,?,?,?,?)
            """,
            (owner_id, "survey_opened", owner_id, f"survey_id={survey_id};visual_test=1", now),
        )
        conn.commit()

        invite_id = int(invite_ids[0])
        token = surveys.derive_invite_token(app.config["SECRET_KEY"], invite_id)
        row = conn.execute(
            "SELECT status,expected_responses FROM surveys WHERE id=?",
            (survey_id,),
        ).fetchone()
        invite = conn.execute(
            "SELECT used FROM survey_invites WHERE id=? AND survey_id=?",
            (invite_id, survey_id),
        ).fetchone()

        if not row or row["status"] != "OPEN" or int(row["expected_responses"]) != 1:
            raise SystemExit("CREATE POSTCHECK FAIL: SURVEY")
        if not invite or int(invite["used"]) != 0:
            raise SystemExit("CREATE POSTCHECK FAIL: INVITE")

        print("VISUAL TEST CREATE: PASS")
        print(f"SURVEY_ID={survey_id}")
        print(f"VISUAL_LINK={PUBLIC_BASE}{token}")
        print("EXPECTED_RESPONSES=1")
        print("INVITE_USED=0")


def cleanup_visual_test(survey_id):
    with app.app_context():
        conn = db()
        row = conn.execute(
            "SELECT id,title FROM surveys WHERE id=?",
            (survey_id,),
        ).fetchone()
        if not row:
            print("VISUAL TEST DATA REMOVED: PASS")
            return
        if not str(row["title"]).startswith(TITLE_PREFIX):
            raise SystemExit("CLEANUP REFUSED: not a visual test survey")

        audit_rows = conn.execute(
            "SELECT id,details FROM audit_log WHERE action LIKE 'survey_%'"
        ).fetchall()
        pattern = re.compile(r"(?:source_|new_)?survey_id=(\d+)")
        for audit_row in audit_rows:
            ids = {int(value) for value in pattern.findall(audit_row["details"] or "")}
            if survey_id in ids:
                conn.execute("DELETE FROM audit_log WHERE id=?", (audit_row["id"],))

        conn.execute("DELETE FROM surveys WHERE id=?", (survey_id,))
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM surveys WHERE id=?",
            (survey_id,),
        ).fetchone()["c"]
        if int(remaining) != 0:
            raise SystemExit("CLEANUP POSTCHECK FAIL")
        print("VISUAL TEST DATA REMOVED: PASS")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("survey_id", type=int)
    args = parser.parse_args()

    if args.command == "create":
        create_visual_test()
    else:
        cleanup_visual_test(args.survey_id)


if __name__ == "__main__":
    main()
