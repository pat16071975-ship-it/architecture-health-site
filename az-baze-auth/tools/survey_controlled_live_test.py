#!/usr/bin/env python3
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(os.environ.get("AZ_SURVEY_TEST_APP_ROOT", "/opt/az-baze-auth"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

OWNER_EMAIL = "balda@inbox.ru"
BASE_URL = os.environ.get("AZ_SURVEY_TEST_BASE_URL", "https://az-baze.ru").rstrip("/")
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
PREFIX = f"AZ_SURVEY_CT_{STAMP}_{os.getpid()}"
MAIN_TITLE = PREFIX + "_MAIN"
SMALL_TITLE = PREFIX + "_SMALL"
TEST_EMAIL = f"survey-test-{STAMP}-{os.getpid()}@invalid.local"
COMMENT_MARKER = PREFIX + "_COMMENT_MARKER"

if not os.environ.get("AZBAZE_SECRET_KEY"):
    raise SystemExit("PRECHECK FAIL: AZBAZE_SECRET_KEY is missing. Load /etc/az-baze/auth.env first.")

import app as app_core
import server
import surveys

app = server.app
DB_PATH = Path(app_core.DB_PATH)
BACKUP_ROOT = Path("/var/lib/az-baze/backups")
BACKUP_DIR = BACKUP_ROOT / f"surveys-controlled-test-{STAMP}"
INSTALL_BACKUP = BACKUP_ROOT / "surveys-fd19efe0-20260918T062724Z"
SURVEY_TABLES = (
    "surveys",
    "survey_sections",
    "survey_questions",
    "survey_options",
    "survey_invites",
    "survey_responses",
    "survey_answers",
)

TEST_SURVEY_IDS = set()
TEMP_UID = None
OWNER_ID = None
BASELINE_COUNTS = {}
NGINX_OFFSETS = {}
JOURNAL_START = None


def step(message):
    print(f"\n=== {message} ===", flush=True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def db_connect(path=None, readonly=False):
    target = Path(path or DB_PATH)
    if readonly:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def http_request(method, path, data=None, timeout=20):
    url = BASE_URL + path
    body = None
    headers = {
        "Accept": "application/json, text/html;q=0.9",
        "User-Agent": "AZ-Survey-Controlled-Test/1.0",
    }
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def json_body(body):
    return json.loads(body.decode("utf-8"))


def backup_database():
    BACKUP_DIR.mkdir(parents=True, exist_ok=False)
    destination = BACKUP_DIR / "auth-before-test.db"
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    src.backup(dst)
    dst.commit()
    dst.close()
    src.close()

    conn = db_connect(destination, readonly=True)
    check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "backup integrity_check failed")
    backup_counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in SURVEY_TABLES
    }
    conn.close()
    check(backup_counts == BASELINE_COUNTS, f"backup survey counts changed: {backup_counts}")
    return destination


def verify_rollback_material():
    check(INSTALL_BACKUP.is_dir(), f"deploy rollback backup missing: {INSTALL_BACKUP}")
    required = [
        INSTALL_BACKUP / "auth.db",
        INSTALL_BACKUP / "files" / "app.py",
        INSTALL_BACKUP / "files" / "server.py",
        INSTALL_BACKUP / "files" / "admin.html",
        INSTALL_BACKUP / "files" / "user_form.html",
    ]
    missing = [str(path) for path in required if not path.exists()]
    check(not missing, "deploy rollback files missing: " + ", ".join(missing))

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        rollback_copy = Path(handle.name)
    try:
        shutil.copy2(INSTALL_BACKUP / "auth.db", rollback_copy)
        conn = db_connect(rollback_copy, readonly=True)
        check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "rollback DB copy integrity failed")
        conn.close()
    finally:
        rollback_copy.unlink(missing_ok=True)


def record_log_offsets():
    global NGINX_OFFSETS, JOURNAL_START
    JOURNAL_START = datetime.now(timezone.utc)
    NGINX_OFFSETS = {}
    log_dir = Path("/var/log/nginx")
    for path in log_dir.glob("*.log"):
        try:
            NGINX_OFFSETS[path] = path.stat().st_size
        except OSError:
            pass


def appended_nginx_logs():
    chunks = []
    current_paths = set(Path("/var/log/nginx").glob("*.log"))
    for path in sorted(current_paths, key=str):
        start = NGINX_OFFSETS.get(path, 0)
        try:
            size = path.stat().st_size
            if size < start:
                start = 0
            with path.open("rb") as handle:
                handle.seek(start)
                chunks.append(handle.read().decode("utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def recent_service_journal():
    since = JOURNAL_START.strftime("%Y-%m-%d %H:%M:%S UTC")
    process = subprocess.run(
        ["journalctl", "-u", "az-baze-auth", "--since", since, "--no-pager"],
        text=True,
        capture_output=True,
        check=False,
    )
    check(process.returncode == 0, "journalctl failed")
    return process.stdout


def cleanup():
    step("CLEANUP TEST DATA")
    try:
        with app.app_context():
            conn = app_core.db()
            found_ids = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM surveys WHERE title LIKE ?",
                    (PREFIX + "%",),
                ).fetchall()
            }
            all_ids = found_ids | TEST_SURVEY_IDS

            if all_ids:
                audit_rows = conn.execute(
                    "SELECT id,details FROM audit_log WHERE action LIKE 'survey_%'"
                ).fetchall()
                for row in audit_rows:
                    ids = {
                        int(value)
                        for value in re.findall(
                            r"(?:source_|new_)?survey_id=(\d+)",
                            row["details"] or "",
                        )
                    }
                    if ids & all_ids:
                        conn.execute("DELETE FROM audit_log WHERE id=?", (row["id"],))

                conn.execute("DELETE FROM surveys WHERE title LIKE ?", (PREFIX + "%",))

            temp_user = conn.execute(
                "SELECT id FROM users WHERE email=? COLLATE NOCASE",
                (TEST_EMAIL,),
            ).fetchone()
            if temp_user:
                uid = temp_user["id"]
                conn.execute(
                    "DELETE FROM audit_log WHERE actor_user_id=? OR target_user_id=?",
                    (uid, uid),
                )
                conn.execute("DELETE FROM permissions WHERE user_id=?", (uid,))
                conn.execute("DELETE FROM users WHERE id=?", (uid,))

            conn.commit()
    except Exception:
        traceback.print_exc()


def verify_clean_state():
    conn = db_connect(readonly=True)
    check(
        conn.execute("SELECT COUNT(*) FROM surveys WHERE title LIKE ?", (PREFIX + "%",)).fetchone()[0] == 0,
        "test surveys remain after cleanup",
    )
    check(
        conn.execute("SELECT COUNT(*) FROM users WHERE email=? COLLATE NOCASE", (TEST_EMAIL,)).fetchone()[0] == 0,
        "temporary user remains after cleanup",
    )

    current_counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in SURVEY_TABLES
    }
    check(current_counts == BASELINE_COUNTS, f"survey table counts not restored: {current_counts}")

    owner = conn.execute(
        "SELECT id,active FROM users WHERE email=? COLLATE NOCASE",
        (OWNER_EMAIL,),
    ).fetchone()
    check(owner and int(owner["active"]) == 1, "owner missing/inactive after cleanup")
    rights = {
        row["section"]
        for row in conn.execute(
            "SELECT section FROM permissions WHERE user_id=?",
            (owner["id"],),
        ).fetchall()
    }
    check({"surveys_create", "surveys_view"} <= rights, "owner survey rights changed")
    check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "live DB integrity_check failed")
    conn.close()


def create_temp_admin():
    global TEMP_UID
    with app.app_context():
        conn = app_core.db()
        conn.execute(
            """
            INSERT INTO users(
                email,full_name,password_hash,is_admin,active,must_change_password,
                failed_attempts,locked_until,created_at,updated_at
            ) VALUES(?,?,?,1,1,0,0,NULL,?,?)
            """,
            (
                TEST_EMAIL,
                "AZ Survey Controlled Test",
                "!",
                app_core.iso_now(),
                app_core.iso_now(),
            ),
        )
        conn.commit()
        TEMP_UID = conn.execute(
            "SELECT id FROM users WHERE email=?",
            (TEST_EMAIL,),
        ).fetchone()["id"]


def session_client(user_id, csrf):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf"] = csrf
    return client


def create_main_survey(owner_client):
    questions = [
        {"section": "Шкалы", "text": "Тестовая оценка A", "type": "scale", "required": True, "options": []},
        {"section": "Шкалы", "text": "Тестовая оценка B", "type": "scale", "required": True, "options": []},
        {"section": "Выбор", "text": "Тестовый один вариант", "type": "single", "required": True, "options": ["Да", "Нет"]},
        {"section": "Выбор", "text": "Тестовый множественный выбор", "type": "multi", "required": True, "options": ["Один", "Два", "Три"]},
        {"section": "Комментарии", "text": "Тестовый комментарий", "type": "text", "required": False, "options": []},
    ]
    response = owner_client.post(
        "/surveys/new",
        data={
            "csrf": "survey-owner",
            "title": MAIN_TITLE,
            "category": "Административные",
            "description": "Только controlled test. Не является реальным опросом.",
            "period_label": "Controlled test",
            "starts_at": "",
            "ends_at": "",
            "expected_responses": "5",
            "questions_json": json.dumps(questions, ensure_ascii=False),
        },
        follow_redirects=False,
    )
    check(response.status_code == 302, f"create main survey status={response.status_code}")
    match = re.search(r"/surveys/(\d+)/", response.headers.get("Location", ""))
    check(match, "main survey id missing")
    survey_id = int(match.group(1))
    TEST_SURVEY_IDS.add(survey_id)
    return survey_id


def open_main_survey(owner_client, survey_id):
    response = owner_client.post(
        f"/surveys/{survey_id}/open",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(response.status_code == 302, f"open main survey status={response.status_code}")
    check(owner_client.get(f"/surveys/{survey_id}/edit").status_code == 409, "OPEN survey remained editable")
    check(owner_client.get(f"/surveys/{survey_id}/results").status_code == 403, "OPEN results were visible")

    with app.app_context():
        conn = app_core.db()
        invites = conn.execute(
            "SELECT id FROM survey_invites WHERE survey_id=? ORDER BY id",
            (survey_id,),
        ).fetchall()
        check(len(invites) == 5, f"invite count={len(invites)} expected=5")
        questions = conn.execute(
            "SELECT id,question_type FROM survey_questions WHERE survey_id=? ORDER BY sort_order",
            (survey_id,),
        ).fetchall()
        check(
            [row["question_type"] for row in questions] == ["scale", "scale", "single", "multi", "text"],
            "question types mismatch",
        )
        option_map = {
            row["id"]: [
                option["value"]
                for option in conn.execute(
                    "SELECT value FROM survey_options WHERE question_id=? ORDER BY sort_order",
                    (row["id"],),
                ).fetchall()
            ]
            for row in questions
        }

    tokens = [
        surveys.derive_invite_token(app.config["SECRET_KEY"], row["id"])
        for row in invites
    ]
    return questions, option_map, tokens


def external_anonymous_flow(survey_id, questions, option_map, tokens):
    q1, q2, q3, q4, q5 = [row["id"] for row in questions]
    single1, single2 = option_map[q3]
    multi1, multi2, multi3 = option_map[q4]

    status, headers, body = http_request("GET", "/survey/")
    check(status == 200, f"public shell status={status}")
    header_map = {key.lower(): value for key, value in headers.items()}
    check("default-src 'self'" in header_map.get("content-security-policy", ""), "public CSP missing")
    check(header_map.get("referrer-policy", "").lower() == "no-referrer", "public Referrer-Policy wrong")
    html = body.decode("utf-8", errors="replace")
    check("location.hash" in html, "fragment token loader missing")
    check("fonts.googleapis" not in html, "external font present on public form")
    check("metrika" not in html.lower() and "webvisor" not in html.lower(), "tracker marker present")

    for _ in range(2):
        status, _, body = http_request(
            "POST",
            "/survey/",
            {"action": "load", "token": tokens[0]},
        )
        check(status == 200, f"repeat load status={status}")
        payload = json_body(body)
        check(payload.get("ok") is True and payload["survey"]["title"] == MAIN_TITLE, "public load payload invalid")

    comment = f"<script>alert(1)</script> {COMMENT_MARKER}"
    submissions = [
        {
            "action": "submit", "token": tokens[0],
            f"q_{q1}": "5", f"q_{q2}": "4",
            f"q_{q3}": single1, f"q_{q4}": [multi1, multi2], f"q_{q5}": comment,
        },
        {
            "action": "submit", "token": tokens[1],
            f"q_{q1}": "3", f"q_{q2}": "NA",
            f"q_{q3}": single2, f"q_{q4}": multi2,
        },
        {
            "action": "submit", "token": tokens[2],
            f"q_{q1}": "1", f"q_{q2}": "NA",
            f"q_{q3}": single1, f"q_{q4}": multi3,
        },
    ]

    for index, data in enumerate(submissions, start=1):
        status, _, body = http_request("POST", "/survey/", data)
        check(status == 200, f"submit {index} status={status} body={body[:200]!r}")
        check(json_body(body).get("ok") is True, f"submit {index} payload invalid")

    with app.app_context():
        conn = app_core.db()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_responses WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["c"]
        check(count == 3, f"response count before parallel={count}")

    parallel_data = {
        "action": "submit", "token": tokens[3],
        f"q_{q1}": "4", f"q_{q2}": "2",
        f"q_{q3}": single1, f"q_{q4}": multi1,
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(http_request, "POST", "/survey/", parallel_data) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]

    statuses = sorted(status for status, _headers, _body in results)
    check(statuses[0] in {200, 409, 410} and statuses[1] in {200, 409, 410}, f"parallel statuses={statuses}")
    check(statuses.count(200) == 1 and len(statuses) == 2, f"parallel double submit not atomic: {statuses}")

    with app.app_context():
        conn = app_core.db()
        response_count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_responses WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["c"]
        used_count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_invites WHERE survey_id=? AND used=1",
            (survey_id,),
        ).fetchone()["c"]
        check(response_count == 4, f"response count after parallel={response_count}")
        check(used_count == 4, f"used invite count={used_count}")

    return {
        "q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5,
        "single1": single1, "single2": single2,
        "multi1": multi1, "multi2": multi2, "multi3": multi3,
    }


def verify_open_state(owner_client, survey_id):
    detail = owner_client.get(f"/surveys/{survey_id}/")
    check(detail.status_code == 200, "OPEN detail unavailable")
    check("4 / 5".encode("utf-8") in detail.data, "OPEN N/M count missing")
    check(owner_client.get(f"/surveys/{survey_id}/results").status_code == 403, "OPEN results endpoint visible")


def close_and_verify_results(owner_client, survey_id, tokens):
    response = owner_client.post(
        f"/surveys/{survey_id}/close",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(response.status_code == 302, f"close status={response.status_code}")

    results = owner_client.get(f"/surveys/{survey_id}/results")
    check(results.status_code == 200, "CLOSED results unavailable")
    html = results.get_data(as_text=True)
    check("&lt;script&gt;alert(1)&lt;/script&gt;" in html, "XSS comment not escaped")
    check("<script>alert(1)</script>" not in html, "raw script rendered")

    exported = owner_client.get(f"/surveys/{survey_id}/export.json")
    check(exported.status_code == 200, "JSON export unavailable")
    payload = exported.get_json()
    check(payload["survey"]["received_responses"] == 4, "JSON response count wrong")
    check(payload["overall_score"] == 3.17, f"overall score={payload['overall_score']} expected=3.17")

    scale = [item for item in payload["questions"] if item["type"] == "scale"]
    check(len(scale) == 2, "scale result count wrong")
    check(scale[0]["mean"] == 3.25 and scale[0]["median"] == 3.5, "scale A stats wrong")
    check(scale[0]["distribution"] == {"1": 1, "2": 0, "3": 1, "4": 1, "5": 1}, "scale A distribution wrong")
    check(scale[1]["mean"] == 3.0 and scale[1]["no_observation"] == 2, "scale B NA handling wrong")
    check(scale[1]["no_observation_ratio"] == 0.5, "scale B NA ratio wrong")
    check(any(item["question"] == "Тестовая оценка B" for item in payload["conclusions"]["insufficient"]), "NA conclusion missing")

    blocks = payload["blocks"]
    check(len(blocks) == 1, f"scale block count={len(blocks)}")
    check(blocks[0]["mean"] == 3.17 and blocks[0]["count"] == 6 and blocks[0]["no_observation"] == 2, "block stats wrong")

    single = next(item for item in payload["questions"] if item["type"] == "single")
    check([item["count"] for item in single["options"]] == [3, 1], "single-choice counts wrong")
    multi = next(item for item in payload["questions"] if item["type"] == "multi")
    check([item["count"] for item in multi["options"]] == [2, 2, 1], "multi-choice counts wrong")

    check(all(set(item.keys()) == {"question", "text"} for item in payload["comments"]), "comment export contains metadata")

    json_text = exported.get_data(as_text=True)
    for forbidden in list(tokens) + ["token_hash", "invite_id", "response_id", "user_id"]:
        check(forbidden not in json_text, f"forbidden identifier in JSON export: {forbidden}")

    csv_response = owner_client.get(f"/surveys/{survey_id}/export.csv")
    check(csv_response.status_code == 200, "CSV export unavailable")
    csv_text = csv_response.get_data(as_text=True)
    for forbidden in list(tokens) + ["token_hash", "invite_id", "response_id", "user_id"]:
        check(forbidden not in csv_text, f"forbidden identifier in CSV export: {forbidden}")

    print_response = owner_client.get(f"/surveys/{survey_id}/print")
    check(print_response.status_code == 200, "print view unavailable")
    print_html = print_response.get_data(as_text=True)
    check("&lt;script&gt;alert(1)&lt;/script&gt;" in print_html, "print XSS escaping missing")

    status, _headers, body = http_request(
        "POST",
        "/survey/",
        {"action": "submit", "token": tokens[4], "q_1": "5"},
    )
    check(status == 410, f"CLOSED survey accepted new answer status={status} body={body[:200]!r}")

    return payload


def verify_granular_rights(survey_id):
    test_client = session_client(TEMP_UID, "survey-temp")

    check(test_client.get("/surveys/").status_code == 403, "admin without survey rights entered section")

    with app.app_context():
        conn = app_core.db()
        conn.execute(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            (TEMP_UID, "surveys_create"),
        )
        conn.commit()

    check(test_client.get("/surveys/").status_code == 200, "create-only cannot see section")
    check(test_client.get("/surveys/new").status_code == 200, "create-only cannot create")
    check(test_client.get(f"/surveys/{survey_id}/invites").status_code == 200, "create-only cannot view invites")
    check(test_client.get(f"/surveys/{survey_id}/results").status_code == 403, "create-only can read results")

    with app.app_context():
        conn = app_core.db()
        conn.execute(
            "DELETE FROM permissions WHERE user_id=? AND section IN ('surveys_create','surveys_view')",
            (TEMP_UID,),
        )
        conn.execute(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            (TEMP_UID, "surveys_view"),
        )
        conn.commit()

    view_client = session_client(TEMP_UID, "survey-temp-view")
    check(view_client.get("/surveys/").status_code == 200, "view-only cannot see section")
    check(view_client.get("/surveys/new").status_code == 403, "view-only can create")
    check(view_client.get(f"/surveys/{survey_id}/invites").status_code == 403, "view-only can view invites")
    check(view_client.get(f"/surveys/{survey_id}/results").status_code == 200, "view-only cannot view results")
    check(view_client.get(f"/surveys/{survey_id}/export.json").status_code == 200, "view-only cannot export")


def clone_and_archive(owner_client, survey_id):
    cloned = owner_client.post(
        f"/surveys/{survey_id}/clone",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(cloned.status_code == 302, "clone failed")
    match = re.search(r"/surveys/(\d+)/edit", cloned.headers.get("Location", ""))
    check(match, "clone id missing")
    clone_id = int(match.group(1))
    TEST_SURVEY_IDS.add(clone_id)

    with app.app_context():
        conn = app_core.db()
        clone = conn.execute(
            "SELECT status,period_label FROM surveys WHERE id=?",
            (clone_id,),
        ).fetchone()
        check(clone and clone["status"] == "DRAFT" and clone["period_label"] == "", "clone metadata wrong")
        check(conn.execute("SELECT COUNT(*) FROM survey_responses WHERE survey_id=?", (clone_id,)).fetchone()[0] == 0, "clone copied responses")
        check(conn.execute("SELECT COUNT(*) FROM survey_invites WHERE survey_id=?", (clone_id,)).fetchone()[0] == 0, "clone copied invites")

    archived = owner_client.post(
        f"/surveys/{survey_id}/archive",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(archived.status_code == 302, "archive failed")
    check(owner_client.get(f"/surveys/{survey_id}/results").status_code == 200, "archived results unavailable")


def small_sample_test(owner_client):
    questions = [
        {
            "section": "Малый блок",
            "text": "Малый тестовый вопрос",
            "type": "scale",
            "required": True,
            "options": [],
        }
    ]
    created = owner_client.post(
        "/surveys/new",
        data={
            "csrf": "survey-owner",
            "title": SMALL_TITLE,
            "category": "Административные",
            "description": "controlled small sample",
            "period_label": "",
            "starts_at": "2026-09-01",
            "ends_at": "2026-09-18",
            "expected_responses": "2",
            "questions_json": json.dumps(questions, ensure_ascii=False),
        },
        follow_redirects=False,
    )
    check(created.status_code == 302, "small survey create failed")
    match = re.search(r"/surveys/(\d+)/", created.headers.get("Location", ""))
    check(match, "small survey id missing")
    survey_id = int(match.group(1))
    TEST_SURVEY_IDS.add(survey_id)

    opened = owner_client.post(
        f"/surveys/{survey_id}/open",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(opened.status_code == 302, "small survey open failed")

    with app.app_context():
        conn = app_core.db()
        invite_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM survey_invites WHERE survey_id=? ORDER BY id",
                (survey_id,),
            ).fetchall()
        ]
        question_id = conn.execute(
            "SELECT id FROM survey_questions WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["id"]

    tokens = [
        surveys.derive_invite_token(app.config["SECRET_KEY"], invite_id)
        for invite_id in invite_ids
    ]

    for token, value in zip(tokens, ("5", "1")):
        response = owner_client.post(
            "/survey/",
            data={"action": "submit", "token": token, f"q_{question_id}": value},
            headers={"Accept": "application/json"},
        )
        check(response.status_code == 200, "small survey submit failed")

    closed = owner_client.post(
        f"/surveys/{survey_id}/close",
        data={"csrf": "survey-owner"},
        follow_redirects=False,
    )
    check(closed.status_code == 302, "small survey close failed")

    payload = owner_client.get(f"/surveys/{survey_id}/export.json").get_json()
    check(payload["survey"]["received_responses"] == 2, "small survey response count wrong")
    check(payload["overall_score"] is None, "small survey exposed overall score with <3 responses")
    html = owner_client.get(f"/surveys/{survey_id}/results").get_data(as_text=True)
    check("Недостаточно данных для сводной оценки" in html, "small sample warning missing")
    check("01.09.2026–18.09.2026" in html, "date-derived period missing")


def verify_schema_privacy():
    conn = db_connect(readonly=True)
    response_columns = {row["name"] for row in conn.execute("PRAGMA table_info(survey_responses)").fetchall()}
    check(response_columns == {"id", "survey_id"}, f"survey_responses columns={response_columns}")

    answer_columns = {row["name"] for row in conn.execute("PRAGMA table_info(survey_answers)").fetchall()}
    forbidden = {"token_id", "token_hash", "user_id", "ip", "user_agent", "created_at", "submitted_at"}
    check(not (answer_columns & forbidden), f"forbidden survey_answers columns={answer_columns & forbidden}")
    conn.close()


def verify_logs(tokens):
    nginx_text = appended_nginx_logs()
    journal_text = recent_service_journal()
    check("/survey/" in nginx_text, "nginx log did not record controlled /survey/ requests")
    check("/survey/" in journal_text, "gunicorn journal did not record controlled /survey/ requests")

    for secret in list(tokens) + [COMMENT_MARKER]:
        check(secret not in nginx_text, "secret/comment appeared in nginx logs")
        check(secret not in journal_text, "secret/comment appeared in gunicorn journal")


def main():
    global OWNER_ID, BASELINE_COUNTS

    step("PRECHECK SERVICE + LIVE STATE")
    service = subprocess.run(
        ["systemctl", "is-active", "--quiet", "az-baze-auth"],
        check=False,
    )
    check(service.returncode == 0, "az-baze-auth is not active")

    status, _headers, body = http_request("GET", "/health")
    check(status == 200 and body.strip() == b"ok", "external health check failed")

    conn = db_connect(readonly=True)
    check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "live DB integrity failed")
    owner = conn.execute(
        "SELECT id,active FROM users WHERE email=? COLLATE NOCASE",
        (OWNER_EMAIL,),
    ).fetchall()
    check(len(owner) == 1 and int(owner[0]["active"]) == 1, "owner account precheck failed")
    OWNER_ID = owner[0]["id"]
    rights = {
        row["section"]
        for row in conn.execute(
            "SELECT section FROM permissions WHERE user_id=?",
            (OWNER_ID,),
        ).fetchall()
    }
    check({"surveys_create", "surveys_view"} <= rights, "owner survey rights missing")
    check(
        conn.execute("SELECT COUNT(*) FROM surveys WHERE title LIKE 'AZ_SURVEY_CT_%'").fetchone()[0] == 0,
        "old controlled-test survey remains",
    )
    check(
        conn.execute("SELECT COUNT(*) FROM users WHERE email LIKE 'survey-test-%@invalid.local'").fetchone()[0] == 0,
        "old controlled-test user remains",
    )
    BASELINE_COUNTS = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in SURVEY_TABLES
    }
    check(BASELINE_COUNTS["surveys"] == 0, "real survey rows already exist; controlled test must stop")
    conn.close()
    print(f"PRECHECK: OK owner_id={OWNER_ID}")

    step("BACKUP + ROLLBACK MATERIAL")
    backup = backup_database()
    verify_rollback_material()
    print(f"BACKUP: {backup}")
    print("ROLLBACK MATERIAL: OK")

    step("FIRST TEMPLATE")
    template = surveys.manager_template_form()
    check(template["title"] == "Оценка управляющей", "manager template title wrong")
    check(len(template["questions"]) == 25, "manager template question count wrong")
    template_text = " ".join(
        [template["title"], template["description"]]
        + [item["text"] for item in template["questions"]]
    ).lower()
    check("2 недели" not in template_text and "две недели" not in template_text, "fixed two-week text remains")
    print("MANAGER TEMPLATE 25 QUESTIONS: OK")

    create_temp_admin()
    owner_client = session_client(OWNER_ID, "survey-owner")

    home = owner_client.get("/")
    check(home.status_code == 200 and "Опросы".encode("utf-8") in home.data, "owner survey menu missing")

    step("CREATE + OPEN")
    main_id = create_main_survey(owner_client)
    check(owner_client.get(f"/surveys/{main_id}/edit").status_code == 200, "DRAFT edit unavailable")
    questions, option_map, tokens = open_main_survey(owner_client, main_id)
    print(f"MAIN SURVEY: OK id={main_id}; invites=5")

    step("EXTERNAL ANONYMOUS FLOW")
    record_log_offsets()
    external_anonymous_flow(main_id, questions, option_map, tokens)
    verify_open_state(owner_client, main_id)
    print("ANONYMOUS LOAD/SUBMIT/PARALLEL ATOMICITY: OK")
    print("OPEN N/M + RESULTS HIDDEN: OK")

    step("CLOSE + RESULTS + EXPORT")
    close_and_verify_results(owner_client, main_id, tokens)
    print("RESULTS/NA/XSS/CSV/JSON/PRINT: OK")

    step("GRANULAR RIGHTS")
    verify_granular_rights(main_id)
    print("CREATE-ONLY + VIEW-ONLY + ADMIN-NO-RIGHTS: OK")

    step("CLONE + ARCHIVE")
    clone_and_archive(owner_client, main_id)
    print("CLONE + ARCHIVE: OK")

    step("SMALL SAMPLE <3")
    small_sample_test(owner_client)
    print("SMALL SAMPLE SUPPRESSION + DATE PERIOD: OK")

    step("SCHEMA PRIVACY")
    verify_schema_privacy()
    print("NO TOKEN/USER/IP/TIMESTAMP LINKAGE IN RESPONSE TABLES: OK")

    step("NGINX + GUNICORN LOG PRIVACY")
    verify_logs(tokens)
    print("SECRET TOKEN + COMMENT TEXT ABSENT FROM LOGS: OK")


if __name__ == "__main__":
    success = False
    try:
        main()
        success = True
    except Exception:
        print("\nCONTROLLED TEST: FAIL", file=sys.stderr)
        traceback.print_exc()
    finally:
        cleanup()
        try:
            verify_clean_state()
            print("CLEANUP + LIVE DB INTEGRITY: OK")
        except Exception:
            success = False
            print("POST-CLEANUP VERIFICATION: FAIL", file=sys.stderr)
            traceback.print_exc()

    if success:
        print("\nCONTROLLED FUNCTIONAL+SECURITY TEST: PASS")
        print("TEST DATA REMOVED: PASS")
        print("CONTROLLED TEST SURVEYS: TECHNICAL PASS")
        print("VISUAL MOBILE CHECK: STILL REQUIRED")
        raise SystemExit(0)
    raise SystemExit(1)
