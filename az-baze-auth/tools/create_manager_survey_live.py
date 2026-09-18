#!/usr/bin/env python3
import os
import sys
from pathlib import Path

APP_ROOT = Path(os.environ.get("AZ_SURVEY_APP_ROOT", "/opt/az-baze-auth"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if not os.environ.get("AZBAZE_SECRET_KEY"):
    raise SystemExit("PRECHECK FAIL: AZBAZE_SECRET_KEY is missing.")

import app as app_core
import server
import surveys

app = server.app
OWNER_EMAIL = "balda@inbox.ru"
TITLE = "Оценка управляющей"


def main():
    with app.app_context():
        conn = app_core.db()

        owner = conn.execute(
            "SELECT id,active FROM users WHERE email=? COLLATE NOCASE",
            (OWNER_EMAIL,),
        ).fetchone()
        if not owner or int(owner["active"]) != 1:
            raise SystemExit("OWNER PRECHECK FAIL")

        owner_id = int(owner["id"])
        rights = {
            row["section"]
            for row in conn.execute(
                "SELECT section FROM permissions WHERE user_id=?",
                (owner_id,),
            ).fetchall()
        }
        if not {"surveys_create", "surveys_view"} <= rights:
            raise SystemExit("OWNER RIGHTS PRECHECK FAIL")

        existing = conn.execute(
            "SELECT id,status FROM surveys WHERE title=? ORDER BY id DESC",
            (TITLE,),
        ).fetchall()
        if existing:
            ids = ", ".join(f"{row['id']}:{row['status']}" for row in existing)
            raise SystemExit(f"CREATE REFUSED: survey already exists ({ids})")

        payload = surveys.manager_template_form()
        payload["period_label"] = ""
        payload["starts_at"] = ""
        payload["ends_at"] = ""
        payload["expected_responses"] = 5

        survey_id = surveys._create_survey(conn, payload, owner_id)
        invite_ids = surveys._create_invites(conn, survey_id, 5)
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
            (owner_id, "survey_created", owner_id, f"survey_id={survey_id}", now),
        )
        conn.execute(
            """
            INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
            VALUES(?,?,?,?,?)
            """,
            (owner_id, "survey_opened", owner_id, f"survey_id={survey_id}", now),
        )
        conn.commit()

        survey = conn.execute(
            """
            SELECT id,title,status,period_label,starts_at,ends_at,expected_responses
            FROM surveys WHERE id=?
            """,
            (survey_id,),
        ).fetchone()
        question_count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_questions WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["c"]
        invite_count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_invites WHERE survey_id=? AND used=0",
            (survey_id,),
        ).fetchone()["c"]

        if (
            not survey
            or survey["title"] != TITLE
            or survey["status"] != "OPEN"
            or (survey["period_label"] or "") != ""
            or (survey["starts_at"] or "") != ""
            or (survey["ends_at"] or "") != ""
            or int(survey["expected_responses"]) != 5
            or int(question_count) != 25
            or int(invite_count) != 5
        ):
            raise SystemExit("POSTCHECK FAIL")

        print("REAL SURVEY CREATE: PASS")
        print(f"SURVEY_ID={survey_id}")
        print("TITLE=Оценка управляющей")
        print("DATES=none")
        print("QUESTIONS=25")
        print("LINKS=5")
        print(f"CABINET_PATH=/surveys/{survey_id}/invites")


if __name__ == "__main__":
    main()
