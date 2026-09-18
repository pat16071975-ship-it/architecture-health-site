import base64
import csv
import hashlib
import hmac
import io
import json
import math
import secrets
import sqlite3
import statistics
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Response, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from app import DB_PATH, audit, csrf_token, db, iso_now, permission_required, require_csrf


CATEGORIES = [
    "Общеклинические",
    "Врачебные",
    "Административные",
    "Средний и младший медицинский персонал",
    "Лаборатория",
    "Прочие",
]

STATUS_LABELS = {
    "DRAFT": "Черновик",
    "OPEN": "Открыт",
    "CLOSED": "Завершён",
    "ARCHIVED": "Архив",
}

QUESTION_TYPE_LABELS = {
    "scale": "Шкала 1–5 + «Не могу оценить»",
    "single": "Один вариант ответа",
    "multi": "Несколько вариантов ответа",
    "text": "Свободный текст",
}

NO_OBSERVATION_RATIO = 0.50
MAX_EXPECTED_RESPONSES = 500
MAX_QUESTIONS = 100
MAX_TEXT_LENGTH = 2000

SURVEY_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    period_label TEXT NOT NULL DEFAULT '',
    starts_at TEXT,
    ends_at TEXT,
    expected_responses INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS survey_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS survey_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL,
    section_id INTEGER,
    question_type TEXT NOT NULL,
    text TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE,
    FOREIGN KEY (section_id) REFERENCES survey_sections(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS survey_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    FOREIGN KEY (question_id) REFERENCES survey_questions(id) ON DELETE CASCADE,
    UNIQUE(question_id, value)
);
CREATE TABLE IF NOT EXISTS survey_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS survey_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL,
    FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS survey_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    numeric_value REAL,
    option_value TEXT,
    text_value TEXT,
    FOREIGN KEY (response_id) REFERENCES survey_responses(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES survey_questions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_surveys_status_category ON surveys(status, category);
CREATE INDEX IF NOT EXISTS idx_survey_questions_survey ON survey_questions(survey_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_survey_invites_survey ON survey_invites(survey_id, used);
CREATE INDEX IF NOT EXISTS idx_survey_responses_survey ON survey_responses(survey_id);
CREATE INDEX IF NOT EXISTS idx_survey_answers_response ON survey_answers(response_id);
CREATE INDEX IF NOT EXISTS idx_survey_answers_question ON survey_answers(question_id);
"""


MANAGER_EVALUATION_INSTRUCTION = (
    "Оценивайте только то, что вы лично наблюдали за указанный период.\n"
    "1 — полностью не согласен\n"
    "2 — скорее не согласен\n"
    "3 — по-разному\n"
    "4 — скорее согласен\n"
    "5 — полностью согласен\n"
    "Если не было возможности оценить ситуацию, выберите «Не могу оценить»."
)

MANAGER_EVALUATION_QUESTIONS = [
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "Управляющая понимает реальную работу администратора и процессы ресепшена.", "scale", True),
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "Задачи ставит понятно и конкретно.", "scale", True),
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "После постановки задачи возвращается к результату и контролирует выполнение.", "scale", True),
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "Разбирается в ситуации до того, как делает выводы или замечания.", "scale", True),
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "В сложной ситуации помогает найти решение, а не только указывает на проблему.", "scale", True),
    ("ПРОФЕССИОНАЛЬНЫЕ КАЧЕСТВА", "Понимает, какие вопросы может решить сама, а какие нужно передать руководству.", "scale", True),
    ("ОРГАНИЗАЦИЯ РАБОТЫ", "Понятно, кто за что отвечает в работе администраторов.", "scale", True),
    ("ОРГАНИЗАЦИЯ РАБОТЫ", "Рабочие договорённости и требования не меняются без объяснения.", "scale", True),
    ("ОРГАНИЗАЦИЯ РАБОТЫ", "Управляющая и старший администратор дают согласованные поручения.", "scale", True),
    ("ОРГАНИЗАЦИЯ РАБОТЫ", "Проблемы смены, расписания, пациентов и ресепшена не остаются без ответственного.", "scale", True),
    ("ОРГАНИЗАЦИЯ РАБОТЫ", "Информация от управляющей приходит вовремя.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Управляющая общается уважительно, в том числе при ошибках сотрудника.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Даёт замечания спокойно, без унижения и публичного давления.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Применяет одинаковые рабочие требования к сотрудникам.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Выслушивает другую точку зрения до принятия решения.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Берёт на себя свою часть ответственности за рабочие решения.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "Если ошиблась или не знает ответа, признаёт это и разбирается.", "scale", True),
    ("ОБЩЕНИЕ И ОТНОШЕНИЕ К КОМАНДЕ", "С ней можно обсуждать рабочую проблему без опасения унижения или наказания за само обращение.", "scale", True),
    ("ОБЩИЕ ВОПРОСЫ", "Мне понятно, чего управляющая ожидает от администраторов.", "scale", True),
    ("ОБЩИЕ ВОПРОСЫ", "Работа администраторского блока стала более организованной.", "scale", True),
    ("ОБЩИЕ ВОПРОСЫ", "На текущем этапе я доверяю управляющей как непосредственному руководителю.", "scale", True),
    ("ОТКРЫТЫЕ ВОПРОСЫ", "Что в работе управляющей за выбранный период было полезным или улучшило работу?", "text", False),
    ("ОТКРЫТЫЕ ВОПРОСЫ", "Что стало сложнее или мешает работе?", "text", False),
    ("ОТКРЫТЫЕ ВОПРОСЫ", "Что управляющей нужно изменить в первую очередь?", "text", False),
    ("ОТКРЫТЫЕ ВОПРОСЫ", "Что ещё важно знать руководству?", "text", False),
]


_rate_lock = threading.Lock()
_rate_events = defaultdict(deque)


def init_surveys_schema(path=None):
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SURVEY_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def derive_invite_token(secret_key, invite_id):
    key = hashlib.sha256(("az-survey-invite-v1|" + str(secret_key)).encode("utf-8")).digest()
    digest = hmac.new(key, str(int(invite_id)).encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _invite_token(invite_id):
    return derive_invite_token(current_app.config["SECRET_KEY"], invite_id)


def _rate_limit(key, action, limit, window_seconds):
    now = time.monotonic()
    bucket_key = (action, key)
    with _rate_lock:
        bucket = _rate_events[bucket_key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        if len(_rate_events) > 5000:
            stale = [k for k, values in _rate_events.items() if not values or values[-1] < cutoff]
            for stale_key in stale[:1000]:
                _rate_events.pop(stale_key, None)
    return True


def _survey_row(survey_id):
    row = db().execute("SELECT * FROM surveys WHERE id=?", (survey_id,)).fetchone()
    if not row:
        abort(404)
    return row


def _response_count(survey_id):
    row = db().execute(
        "SELECT COUNT(*) AS c FROM survey_responses WHERE survey_id=?",
        (survey_id,),
    ).fetchone()
    return int(row["c"] or 0)


def _structure(survey_id):
    section_rows = db().execute(
        "SELECT * FROM survey_sections WHERE survey_id=? ORDER BY sort_order,id",
        (survey_id,),
    ).fetchall()
    question_rows = db().execute(
        "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY sort_order,id",
        (survey_id,),
    ).fetchall()
    option_rows = db().execute(
        """
        SELECT o.*
        FROM survey_options o
        JOIN survey_questions q ON q.id=o.question_id
        WHERE q.survey_id=?
        ORDER BY o.question_id,o.sort_order,o.id
        """,
        (survey_id,),
    ).fetchall()
    options = defaultdict(list)
    for row in option_rows:
        options[row["question_id"]].append(dict(row))

    sections = []
    by_id = {}
    for row in section_rows:
        item = dict(row)
        item["questions"] = []
        sections.append(item)
        by_id[item["id"]] = item

    ungrouped = []
    questions = []
    for row in question_rows:
        item = dict(row)
        item["required"] = bool(item["required"])
        item["options"] = options.get(item["id"], [])
        questions.append(item)
        if item["section_id"] in by_id:
            by_id[item["section_id"]]["questions"].append(item)
        else:
            ungrouped.append(item)

    if ungrouped:
        sections.append({"id": None, "title": "Без раздела", "sort_order": 999999, "questions": ungrouped})
    return sections, questions


def _validate_date(value, label):
    value = (value or "").strip()
    if not value:
        return None
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Некорректное поле «{label}».") from exc
    return value


def _normalize_questions(raw):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError("Не удалось прочитать список вопросов.") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("Добавьте хотя бы один вопрос.")
    if len(value) > MAX_QUESTIONS:
        raise ValueError(f"В одном опросе допускается не более {MAX_QUESTIONS} вопросов.")

    result = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Вопрос {index}: некорректная структура.")
        text_value = str(item.get("text") or "").strip()
        section = str(item.get("section") or "").strip()
        question_type = str(item.get("type") or "").strip()
        required = bool(item.get("required"))
        if not text_value:
            raise ValueError(f"Вопрос {index}: укажите текст.")
        if len(text_value) > 1200:
            raise ValueError(f"Вопрос {index}: текст слишком длинный.")
        if len(section) > 160:
            raise ValueError(f"Вопрос {index}: название блока слишком длинное.")
        if question_type not in QUESTION_TYPE_LABELS:
            raise ValueError(f"Вопрос {index}: неизвестный тип вопроса.")

        labels = []
        if question_type in {"single", "multi"}:
            raw_options = item.get("options")
            if isinstance(raw_options, str):
                raw_options = raw_options.splitlines()
            if not isinstance(raw_options, list):
                raw_options = []
            seen = set()
            for option in raw_options:
                label = str(option or "").strip()
                if not label or label in seen:
                    continue
                if len(label) > 300:
                    raise ValueError(f"Вопрос {index}: вариант ответа слишком длинный.")
                seen.add(label)
                labels.append(label)
            if len(labels) < 2:
                raise ValueError(f"Вопрос {index}: добавьте минимум два варианта ответа.")
            if len(labels) > 30:
                raise ValueError(f"Вопрос {index}: слишком много вариантов ответа.")

        result.append(
            {
                "section": section,
                "text": text_value,
                "type": question_type,
                "required": required,
                "options": labels,
            }
        )
    return result


def _survey_payload_from_request():
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    period_label = request.form.get("period_label", "").strip()
    starts_at = _validate_date(request.form.get("starts_at"), "Дата начала")
    ends_at = _validate_date(request.form.get("ends_at"), "Дата окончания")
    if not title:
        raise ValueError("Название опроса обязательно.")
    if len(title) > 240:
        raise ValueError("Название опроса слишком длинное.")
    if category not in CATEGORIES:
        raise ValueError("Выберите категорию.")
    if len(description) > 4000:
        raise ValueError("Описание слишком длинное.")
    if len(period_label) > 240:
        raise ValueError("Период оценки слишком длинный.")
    if starts_at and ends_at and starts_at > ends_at:
        raise ValueError("Дата окончания не может быть раньше даты начала.")
    try:
        expected = int(request.form.get("expected_responses", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Укажите ожидаемое число участников.") from exc
    if expected < 1 or expected > MAX_EXPECTED_RESPONSES:
        raise ValueError(f"Ожидаемое число участников должно быть от 1 до {MAX_EXPECTED_RESPONSES}.")
    questions = _normalize_questions(request.form.get("questions_json", ""))
    return {
        "title": title,
        "category": category,
        "description": description,
        "period_label": period_label,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "expected_responses": expected,
        "questions": questions,
    }


def _insert_questions(conn, survey_id, questions):
    section_ids = {}
    next_section_order = 1
    for order, question in enumerate(questions, start=1):
        section_title = question["section"]
        section_id = None
        if section_title:
            section_id = section_ids.get(section_title)
            if section_id is None:
                cur = conn.execute(
                    "INSERT INTO survey_sections(survey_id,title,sort_order) VALUES(?,?,?)",
                    (survey_id, section_title, next_section_order),
                )
                section_id = cur.lastrowid
                section_ids[section_title] = section_id
                next_section_order += 1
        cur = conn.execute(
            """
            INSERT INTO survey_questions(survey_id,section_id,question_type,text,required,sort_order)
            VALUES(?,?,?,?,?,?)
            """,
            (
                survey_id,
                section_id,
                question["type"],
                question["text"],
                1 if question["required"] else 0,
                order,
            ),
        )
        question_id = cur.lastrowid
        for option_order, label in enumerate(question["options"], start=1):
            conn.execute(
                "INSERT INTO survey_options(question_id,value,label,sort_order) VALUES(?,?,?,?)",
                (question_id, f"opt{option_order}", label, option_order),
            )


def _form_data_from_survey(survey):
    sections, _questions = _structure(survey["id"])
    question_data = []
    for section in sections:
        section_title = "" if section["id"] is None else section["title"]
        for question in section["questions"]:
            question_data.append(
                {
                    "section": section_title,
                    "text": question["text"],
                    "type": question["question_type"],
                    "required": bool(question["required"]),
                    "options": [option["label"] for option in question["options"]],
                    "sort_order": question["sort_order"],
                }
            )
    question_data.sort(key=lambda item: item["sort_order"])
    return {
        "title": survey["title"],
        "category": survey["category"],
        "description": survey["description"] or "",
        "period_label": survey["period_label"] or "",
        "starts_at": survey["starts_at"] or "",
        "ends_at": survey["ends_at"] or "",
        "expected_responses": survey["expected_responses"],
        "questions": question_data,
    }


def manager_template_form():
    return {
        "title": "Оценка управляющей",
        "category": "Административные",
        "description": MANAGER_EVALUATION_INSTRUCTION,
        "period_label": "",
        "starts_at": "",
        "ends_at": "",
        "expected_responses": 5,
        "questions": [
            {
                "section": section,
                "text": text_value,
                "type": question_type,
                "required": required,
                "options": [],
                "sort_order": index,
            }
            for index, (section, text_value, question_type, required) in enumerate(
                MANAGER_EVALUATION_QUESTIONS, start=1
            )
        ],
    }


def _create_survey(conn, payload, creator_id):
    cur = conn.execute(
        """
        INSERT INTO surveys(
            title,category,description,period_label,starts_at,ends_at,
            expected_responses,status,created_by_user_id,created_at,closed_at
        ) VALUES(?,?,?,?,?,?,?,'DRAFT',?,?,NULL)
        """,
        (
            payload["title"],
            payload["category"],
            payload["description"],
            payload["period_label"],
            payload["starts_at"],
            payload["ends_at"],
            payload["expected_responses"],
            creator_id,
            iso_now(),
        ),
    )
    survey_id = cur.lastrowid
    _insert_questions(conn, survey_id, payload["questions"])
    return survey_id


def _create_invites(conn, survey_id, count):
    invite_ids = []
    for _ in range(count):
        placeholder = secrets.token_hex(32)
        cur = conn.execute(
            "INSERT INTO survey_invites(survey_id,token_hash,used) VALUES(?,?,0)",
            (survey_id, placeholder),
        )
        invite_id = cur.lastrowid
        token = _invite_token(invite_id)
        conn.execute(
            "UPDATE survey_invites SET token_hash=? WHERE id=?",
            (token_hash(token), invite_id),
        )
        invite_ids.append(invite_id)
    return invite_ids


def record_response(conn, survey_id, invite_token_hash, answer_rows):
    try:
        conn.execute("BEGIN IMMEDIATE")
        survey = conn.execute(
            "SELECT status FROM surveys WHERE id=?",
            (survey_id,),
        ).fetchone()
        invite = conn.execute(
            "SELECT id,used FROM survey_invites WHERE survey_id=? AND token_hash=?",
            (survey_id, invite_token_hash),
        ).fetchone()
        if not survey or survey["status"] != "OPEN" or not invite or int(invite["used"]):
            conn.rollback()
            return False

        cur = conn.execute(
            "INSERT INTO survey_responses(survey_id) VALUES(?)",
            (survey_id,),
        )
        response_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO survey_answers(response_id,question_id,numeric_value,option_value,text_value)
            VALUES(?,?,?,?,?)
            """,
            [
                (response_id, question_id, numeric_value, option_value, text_value)
                for question_id, numeric_value, option_value, text_value in answer_rows
            ],
        )
        updated = conn.execute(
            "UPDATE survey_invites SET used=1 WHERE id=? AND used=0",
            (invite["id"],),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def _submitted_values():
    return {key: request.form.getlist(key) for key in request.form.keys()}


def _answer_rows_for_request(questions):
    rows = []
    errors = []
    for question in questions:
        question_id = question["id"]
        field = f"q_{question_id}"
        question_type = question["question_type"]
        required = bool(question["required"])
        valid_options = {item["value"] for item in question["options"]}

        if question_type == "scale":
            value = request.form.get(field, "").strip()
            if not value:
                if required:
                    errors.append(f"Ответьте на обязательный вопрос: {question['text']}")
                continue
            if value == "NA":
                rows.append((question_id, None, "NA", None))
            elif value in {"1", "2", "3", "4", "5"}:
                rows.append((question_id, float(value), None, None))
            else:
                errors.append("Получено недопустимое значение шкалы.")
        elif question_type == "single":
            value = request.form.get(field, "").strip()
            if not value:
                if required:
                    errors.append(f"Ответьте на обязательный вопрос: {question['text']}")
                continue
            if value not in valid_options:
                errors.append("Получен неизвестный вариант ответа.")
                continue
            rows.append((question_id, None, value, None))
        elif question_type == "multi":
            values = []
            for value in request.form.getlist(field):
                value = value.strip()
                if value and value not in values:
                    values.append(value)
            if not values:
                if required:
                    errors.append(f"Ответьте на обязательный вопрос: {question['text']}")
                continue
            if any(value not in valid_options for value in values):
                errors.append("Получен неизвестный вариант ответа.")
                continue
            for value in values:
                rows.append((question_id, None, value, None))
        elif question_type == "text":
            value = request.form.get(field, "")
            if len(value) > MAX_TEXT_LENGTH:
                errors.append(f"Комментарий не может быть длиннее {MAX_TEXT_LENGTH} символов.")
                continue
            value = value.strip()
            if not value:
                if required:
                    errors.append(f"Ответьте на обязательный вопрос: {question['text']}")
                continue
            rows.append((question_id, None, None, value))
    if errors:
        raise ValueError(errors[0])
    return rows


def _score_label(value):
    if value is None:
        return ""
    if value >= 4.3:
        return "сильная зона"
    if value >= 3.6:
        return "в целом хорошо"
    if value >= 3.0:
        return "требует внимания"
    return "проблемная зона"


def calculate_scale_stats(numeric_values, no_observation_count):
    values = [float(value) for value in numeric_values if value is not None]
    distribution = {str(number): 0 for number in range(1, 6)}
    for value in values:
        rounded = int(value)
        if 1 <= rounded <= 5:
            distribution[str(rounded)] += 1
    mean_value = sum(values) / len(values) if values else None
    median_value = statistics.median(values) if values else None
    total = len(values) + int(no_observation_count or 0)
    return {
        "count": len(values),
        "mean": round(mean_value, 2) if mean_value is not None else None,
        "median": round(float(median_value), 2) if median_value is not None else None,
        "distribution": distribution,
        "no_observation": int(no_observation_count or 0),
        "no_observation_ratio": (int(no_observation_count or 0) / total) if total else 0.0,
        "zone": _score_label(mean_value),
    }


def _results_payload(survey):
    sections, questions = _structure(survey["id"])
    answer_rows = db().execute(
        """
        SELECT a.question_id,a.numeric_value,a.option_value,a.text_value
        FROM survey_answers a
        JOIN survey_responses r ON r.id=a.response_id
        WHERE r.survey_id=?
        """,
        (survey["id"],),
    ).fetchall()
    answers_by_question = defaultdict(list)
    for row in answer_rows:
        answers_by_question[row["question_id"]].append(dict(row))

    section_title_by_id = {section["id"]: section["title"] for section in sections if section["id"] is not None}
    question_results = []
    block_values = defaultdict(list)
    block_no_observation = defaultdict(int)
    comments = []

    for question in questions:
        rows = answers_by_question.get(question["id"], [])
        item = {
            "section": section_title_by_id.get(question["section_id"], ""),
            "question": question["text"],
            "type": question["question_type"],
            "type_label": QUESTION_TYPE_LABELS[question["question_type"]],
            "required": bool(question["required"]),
            "sort_order": question["sort_order"],
        }
        if question["question_type"] == "scale":
            numeric = [row["numeric_value"] for row in rows if row["numeric_value"] is not None]
            no_observation = sum(1 for row in rows if row["option_value"] == "NA")
            item.update(calculate_scale_stats(numeric, no_observation))
            if item["section"]:
                if numeric:
                    block_values[item["section"]].extend(float(value) for value in numeric)
                block_no_observation[item["section"]] += no_observation
        elif question["question_type"] in {"single", "multi"}:
            option_counts = {option["value"]: 0 for option in question["options"]}
            for row in rows:
                if row["option_value"] in option_counts:
                    option_counts[row["option_value"]] += 1
            item["count"] = sum(option_counts.values())
            item["options"] = [
                {"label": option["label"], "count": option_counts[option["value"]]}
                for option in question["options"]
            ]
        else:
            texts = [row["text_value"] for row in rows if row["text_value"]]
            item["count"] = len(texts)
            for text_value in texts:
                comments.append({"question": question["text"], "text": text_value})
        question_results.append(item)

    block_results = []
    for section in sections:
        title = section["title"]
        values = block_values.get(title, [])
        if not values:
            continue
        mean_value = sum(values) / len(values)
        block_results.append(
            {
                "title": title,
                "mean": round(mean_value, 2),
                "count": len(values),
                "no_observation": block_no_observation.get(title, 0),
                "zone": _score_label(mean_value),
            }
        )

    response_count = _response_count(survey["id"])
    expected = int(survey["expected_responses"] or 0)
    overall = None
    if response_count >= 3 and block_results:
        overall = round(sum(item["mean"] for item in block_results) / len(block_results), 2)

    scale_results = [item for item in question_results if item["type"] == "scale" and item.get("mean") is not None]
    strongest = sorted(scale_results, key=lambda item: (-item["mean"], item["sort_order"]))[:3]
    attention = sorted(scale_results, key=lambda item: (item["mean"], item["sort_order"]))[:3]
    insufficient = [
        item
        for item in question_results
        if item["type"] == "scale"
        and item.get("no_observation_ratio", 0) >= NO_OBSERVATION_RATIO
        and (item.get("count", 0) + item.get("no_observation", 0)) > 0
    ]
    best_block = max(block_results, key=lambda item: item["mean"], default=None)
    weakest_block = min(block_results, key=lambda item: item["mean"], default=None)

    comments.sort(
        key=lambda item: hashlib.sha256(
            (item["question"] + "\0" + item["text"]).encode("utf-8")
        ).hexdigest()
    )

    return {
        "survey": {
            "title": survey["title"],
            "category": survey["category"],
            "period": survey["period_label"] or "",
            "status": survey["status"],
            "status_label": STATUS_LABELS[survey["status"]],
            "expected_responses": expected,
            "received_responses": response_count,
            "participation": round(response_count / expected, 4) if expected else None,
        },
        "questions": question_results,
        "blocks": block_results,
        "overall_score": overall,
        "overall_zone": _score_label(overall),
        "enough_for_overall": response_count >= 3,
        "conclusions": {
            "strongest": strongest,
            "attention": attention,
            "insufficient": insufficient,
            "best_block": best_block,
            "weakest_block": weakest_block,
        },
        "comments": comments,
    }


def _public_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def register_surveys(app):
    init_surveys_schema()

    @app.after_request
    def survey_public_security_headers(response):
        if request.path.startswith("/survey/"):
            return _public_headers(response)
        return response

    @app.get("/surveys/")
    @permission_required("surveys")
    def surveys_index():
        category = request.args.get("category", "").strip()
        status = request.args.get("status", "").strip()
        period = request.args.get("period", "").strip()
        where = []
        params = []
        if category:
            if category not in CATEGORIES:
                abort(400)
            where.append("s.category=?")
            params.append(category)
        if status:
            if status not in STATUS_LABELS:
                abort(400)
            where.append("s.status=?")
            params.append(status)
        if period:
            where.append("s.period_label LIKE ?")
            params.append(f"%{period}%")
        sql = """
            SELECT s.*, COUNT(r.id) AS response_count
            FROM surveys s
            LEFT JOIN survey_responses r ON r.survey_id=s.id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY s.id ORDER BY s.created_at DESC,s.id DESC"
        rows = db().execute(sql, tuple(params)).fetchall()
        groups = {
            category_name: {key: [] for key in ("DRAFT", "OPEN", "CLOSED", "ARCHIVED")}
            for category_name in CATEGORIES
        }
        for row in rows:
            groups[row["category"]][row["status"]].append(dict(row))
        return render_template(
            "surveys_list.html",
            groups=groups,
            categories=CATEGORIES,
            status_labels=STATUS_LABELS,
            filters={"category": category, "status": status, "period": period},
            csrf=csrf_token(),
        )

    @app.route("/surveys/new", methods=["GET", "POST"])
    @permission_required("surveys")
    def survey_new():
        error = None
        form_data = manager_template_form() if request.args.get("template") == "manager" else {
            "title": "",
            "category": "Общеклинические",
            "description": "",
            "period_label": "",
            "starts_at": "",
            "ends_at": "",
            "expected_responses": 1,
            "questions": [],
        }
        if request.method == "POST":
            require_csrf()
            try:
                payload = _survey_payload_from_request()
                conn = db()
                conn.execute("BEGIN")
                survey_id = _create_survey(conn, payload, g.user["id"])
                conn.commit()
                audit("survey_created", target_user_id=g.user["id"], details=f"survey_id={survey_id}")
                return redirect(url_for("survey_detail", survey_id=survey_id))
            except ValueError as exc:
                error = str(exc)
                try:
                    form_data = {
                        "title": request.form.get("title", ""),
                        "category": request.form.get("category", ""),
                        "description": request.form.get("description", ""),
                        "period_label": request.form.get("period_label", ""),
                        "starts_at": request.form.get("starts_at", ""),
                        "ends_at": request.form.get("ends_at", ""),
                        "expected_responses": request.form.get("expected_responses", ""),
                        "questions": json.loads(request.form.get("questions_json") or "[]"),
                    }
                except ValueError:
                    pass
        return render_template(
            "survey_form.html",
            form_data=form_data,
            categories=CATEGORIES,
            question_types=QUESTION_TYPE_LABELS,
            title="Создать опрос",
            error=error,
            csrf=csrf_token(),
        )

    @app.route("/surveys/<int:survey_id>/edit", methods=["GET", "POST"])
    @permission_required("surveys")
    def survey_edit(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] != "DRAFT":
            abort(409)
        error = None
        form_data = _form_data_from_survey(survey)
        if request.method == "POST":
            require_csrf()
            try:
                payload = _survey_payload_from_request()
                conn = db()
                conn.execute("BEGIN")
                current = conn.execute("SELECT status FROM surveys WHERE id=?", (survey_id,)).fetchone()
                if not current or current["status"] != "DRAFT":
                    conn.rollback()
                    abort(409)
                conn.execute(
                    """
                    UPDATE surveys
                    SET title=?,category=?,description=?,period_label=?,starts_at=?,ends_at=?,expected_responses=?
                    WHERE id=?
                    """,
                    (
                        payload["title"],
                        payload["category"],
                        payload["description"],
                        payload["period_label"],
                        payload["starts_at"],
                        payload["ends_at"],
                        payload["expected_responses"],
                        survey_id,
                    ),
                )
                conn.execute("DELETE FROM survey_questions WHERE survey_id=?", (survey_id,))
                conn.execute("DELETE FROM survey_sections WHERE survey_id=?", (survey_id,))
                _insert_questions(conn, survey_id, payload["questions"])
                conn.commit()
                audit("survey_edited", target_user_id=g.user["id"], details=f"survey_id={survey_id}")
                return redirect(url_for("survey_detail", survey_id=survey_id))
            except ValueError as exc:
                error = str(exc)
                try:
                    form_data = {
                        "title": request.form.get("title", ""),
                        "category": request.form.get("category", ""),
                        "description": request.form.get("description", ""),
                        "period_label": request.form.get("period_label", ""),
                        "starts_at": request.form.get("starts_at", ""),
                        "ends_at": request.form.get("ends_at", ""),
                        "expected_responses": request.form.get("expected_responses", ""),
                        "questions": json.loads(request.form.get("questions_json") or "[]"),
                    }
                except ValueError:
                    pass
        return render_template(
            "survey_form.html",
            form_data=form_data,
            categories=CATEGORIES,
            question_types=QUESTION_TYPE_LABELS,
            title="Редактировать опрос",
            error=error,
            csrf=csrf_token(),
        )

    @app.get("/surveys/<int:survey_id>/")
    @permission_required("surveys")
    def survey_detail(survey_id):
        survey = _survey_row(survey_id)
        sections, questions = _structure(survey_id)
        return render_template(
            "survey_detail.html",
            survey=survey,
            sections=sections,
            question_count=len(questions),
            response_count=_response_count(survey_id),
            status_labels=STATUS_LABELS,
            type_labels=QUESTION_TYPE_LABELS,
            csrf=csrf_token(),
        )

    @app.post("/surveys/<int:survey_id>/open")
    @permission_required("surveys")
    def survey_open(survey_id):
        require_csrf()
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        survey = conn.execute("SELECT * FROM surveys WHERE id=?", (survey_id,)).fetchone()
        if not survey:
            conn.rollback()
            abort(404)
        if survey["status"] != "DRAFT":
            conn.rollback()
            abort(409)
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_questions WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["c"]
        if not count:
            conn.rollback()
            abort(400)
        existing_invites = conn.execute(
            "SELECT COUNT(*) AS c FROM survey_invites WHERE survey_id=?",
            (survey_id,),
        ).fetchone()["c"]
        if existing_invites:
            conn.rollback()
            abort(409)
        _create_invites(conn, survey_id, int(survey["expected_responses"]))
        conn.execute("UPDATE surveys SET status='OPEN' WHERE id=?", (survey_id,))
        conn.commit()
        audit(
            "survey_opened",
            target_user_id=g.user["id"],
            details=f"survey_id={survey_id}; invites={survey['expected_responses']}",
        )
        return redirect(url_for("survey_invites", survey_id=survey_id))

    @app.post("/surveys/<int:survey_id>/close")
    @permission_required("surveys")
    def survey_close(survey_id):
        require_csrf()
        updated = db().execute(
            "UPDATE surveys SET status='CLOSED',closed_at=? WHERE id=? AND status='OPEN'",
            (iso_now(), survey_id),
        )
        db().commit()
        if updated.rowcount != 1:
            abort(409)
        audit("survey_closed", target_user_id=g.user["id"], details=f"survey_id={survey_id}")
        return redirect(url_for("survey_results", survey_id=survey_id))

    @app.post("/surveys/<int:survey_id>/archive")
    @permission_required("surveys")
    def survey_archive(survey_id):
        require_csrf()
        updated = db().execute(
            "UPDATE surveys SET status='ARCHIVED' WHERE id=? AND status='CLOSED'",
            (survey_id,),
        )
        db().commit()
        if updated.rowcount != 1:
            abort(409)
        audit("survey_archived", target_user_id=g.user["id"], details=f"survey_id={survey_id}")
        return redirect(url_for("surveys_index"))

    @app.get("/surveys/<int:survey_id>/invites")
    @permission_required("surveys")
    def survey_invites(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] == "DRAFT":
            abort(409)
        rows = db().execute(
            "SELECT id,token_hash,used FROM survey_invites WHERE survey_id=? ORDER BY id",
            (survey_id,),
        ).fetchall()
        links = []
        for row in rows:
            token = _invite_token(row["id"])
            valid = hmac.compare_digest(token_hash(token), row["token_hash"])
            links.append(
                {
                    "used": bool(row["used"]),
                    "valid": valid,
                    "url": (
                        request.url_root.rstrip("/") + url_for("survey_public", token=token)
                        if valid else ""
                    ),
                }
            )
        return render_template(
            "survey_invites.html",
            survey=survey,
            links=links,
            response_count=_response_count(survey_id),
            csrf=csrf_token(),
        )

    @app.get("/surveys/<int:survey_id>/results")
    @permission_required("surveys")
    def survey_results(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] not in {"CLOSED", "ARCHIVED"}:
            abort(403)
        results = _results_payload(survey)
        return render_template(
            "survey_results.html",
            survey=survey,
            results=results,
            csrf=csrf_token(),
        )

    @app.get("/surveys/<int:survey_id>/export.json")
    @permission_required("surveys")
    def survey_export_json(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] not in {"CLOSED", "ARCHIVED"}:
            abort(403)
        payload = _results_payload(survey)
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        response = Response(body, mimetype="application/json")
        response.headers["Content-Disposition"] = f'attachment; filename="survey-{survey_id}-results.json"'
        return response

    @app.get("/surveys/<int:survey_id>/export.csv")
    @permission_required("surveys")
    def survey_export_csv(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] not in {"CLOSED", "ARCHIVED"}:
            abort(403)
        results = _results_payload(survey)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Опрос", results["survey"]["title"]])
        writer.writerow(["Категория", results["survey"]["category"]])
        writer.writerow(["Период", results["survey"]["period"]])
        writer.writerow(["Ожидалось", results["survey"]["expected_responses"]])
        writer.writerow(["Получено", results["survey"]["received_responses"]])
        writer.writerow([])
        writer.writerow([
            "Блок", "Вопрос", "Тип", "Количество", "Среднее", "Медиана",
            "1", "2", "3", "4", "5", "Не могу оценить", "Распределение вариантов"
        ])
        for item in results["questions"]:
            distribution = item.get("distribution") or {}
            options = item.get("options") or []
            writer.writerow([
                item["section"],
                item["question"],
                item["type_label"],
                item.get("count", ""),
                item.get("mean", ""),
                item.get("median", ""),
                distribution.get("1", ""),
                distribution.get("2", ""),
                distribution.get("3", ""),
                distribution.get("4", ""),
                distribution.get("5", ""),
                item.get("no_observation", ""),
                " | ".join(f"{option['label']}: {option['count']}" for option in options),
            ])
        writer.writerow([])
        writer.writerow(["Комментарии сотрудников"])
        writer.writerow(["Вопрос", "Комментарий"])
        for comment in results["comments"]:
            writer.writerow([comment["question"], comment["text"]])
        body = "\ufeff" + output.getvalue()
        response = Response(body, mimetype="text/csv")
        response.headers["Content-Disposition"] = f'attachment; filename="survey-{survey_id}-results.csv"'
        return response

    @app.get("/surveys/<int:survey_id>/print")
    @permission_required("surveys")
    def survey_print(survey_id):
        survey = _survey_row(survey_id)
        if survey["status"] not in {"CLOSED", "ARCHIVED"}:
            abort(403)
        return render_template("survey_print.html", results=_results_payload(survey))

    @app.post("/surveys/<int:survey_id>/clone")
    @permission_required("surveys")
    def survey_clone(survey_id):
        require_csrf()
        survey = _survey_row(survey_id)
        if survey["status"] != "CLOSED":
            abort(409)
        form_data = _form_data_from_survey(survey)
        payload = {
            "title": form_data["title"],
            "category": form_data["category"],
            "description": form_data["description"],
            "period_label": "",
            "starts_at": None,
            "ends_at": None,
            "expected_responses": int(form_data["expected_responses"]),
            "questions": [
                {
                    "section": item["section"],
                    "text": item["text"],
                    "type": item["type"],
                    "required": bool(item["required"]),
                    "options": list(item["options"]),
                }
                for item in form_data["questions"]
            ],
        }
        conn = db()
        conn.execute("BEGIN")
        new_id = _create_survey(conn, payload, g.user["id"])
        conn.commit()
        audit(
            "survey_cloned",
            target_user_id=g.user["id"],
            details=f"source_survey_id={survey_id}; new_survey_id={new_id}",
        )
        return redirect(url_for("survey_edit", survey_id=new_id))

    @app.route("/survey/<token>", methods=["GET", "POST"])
    def survey_public(token):
        digest = token_hash(token)
        action = "post" if request.method == "POST" else "get"
        if not _rate_limit(digest, action, 12 if action == "post" else 60, 600):
            response = Response(
                render_template(
                    "survey_public_message.html",
                    title="Слишком много запросов",
                    message="Попробуйте ещё раз немного позже.",
                ),
                status=429,
                mimetype="text/html",
            )
            return response

        invite = db().execute(
            """
            SELECT i.id AS invite_id,i.used,s.*
            FROM survey_invites i
            JOIN surveys s ON s.id=i.survey_id
            WHERE i.token_hash=?
            """,
            (digest,),
        ).fetchone()
        if not invite:
            return Response(
                render_template(
                    "survey_public_message.html",
                    title="Ссылка недействительна",
                    message="Проверьте адрес ссылки.",
                ),
                status=404,
                mimetype="text/html",
            )
        if int(invite["used"]):
            return Response(
                render_template(
                    "survey_public_message.html",
                    title="Ответ уже отправлен",
                    message="Эта одноразовая ссылка уже использована.",
                ),
                status=410,
                mimetype="text/html",
            )
        if invite["status"] != "OPEN":
            return Response(
                render_template(
                    "survey_public_message.html",
                    title="Опрос закрыт",
                    message="Этот опрос сейчас не принимает ответы.",
                ),
                status=410,
                mimetype="text/html",
            )

        sections, questions = _structure(invite["survey_id"])
        survey = dict(invite)
        error = None
        submitted = _submitted_values()
        if request.method == "POST":
            try:
                answer_rows = _answer_rows_for_request(questions)
                if not record_response(db(), invite["survey_id"], digest, answer_rows):
                    raise RuntimeError("Ссылка уже использована или опрос закрыт.")
                if request.headers.get("X-AZ-Survey") == "1":
                    return jsonify(ok=True, message="Спасибо. Ваш ответ принят.")
                return render_template("survey_public_thanks.html")
            except ValueError as exc:
                error = str(exc)
            except RuntimeError as exc:
                error = str(exc)

            if request.headers.get("X-AZ-Survey") == "1":
                return jsonify(ok=False, error=error or "Не удалось сохранить ответ."), 400

        return render_template(
            "survey_public.html",
            survey=survey,
            sections=sections,
            error=error,
            submitted=submitted,
            max_text_length=MAX_TEXT_LENGTH,
        )
