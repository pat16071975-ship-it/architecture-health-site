"""Immutable incoming-document inbox for Medрасходка.

A document is only evidence pending human review. Saving or reviewing it never
changes inventory receipts, stock balances, purchases, or finance data.
Original bytes are stored transactionally in the protected AZ BAZE SQLite DB.
"""
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import date, datetime, timezone
from pathlib import PurePath

from flask import abort, g, redirect, render_template, request, send_file, url_for

MAX_BYTES = 20 * 1024 * 1024
MAX_RESULTS = 50
TYPES = (
    ("unknown", "Не определён"),
    ("upd", "УПД"),
    ("waybill", "Накладная"),
    ("invoice", "Счёт-фактура"),
    ("bill", "Счёт на оплату"),
    ("act", "Акт"),
    ("receipt", "Чек"),
    ("contract", "Договор"),
    ("payment", "Платёжный документ"),
    ("other", "Другой документ"),
)
STATUSES = (
    ("pending", "На разбор"),
    ("review", "В работе"),
    ("reviewed", "Разобран"),
)
TYPE_KEYS = {key for key, _ in TYPES}
STATUS_KEYS = {key for key, _ in STATUSES}

SCHEMA = """
CREATE TABLE IF NOT EXISTS med_supply_documents (
 id INTEGER PRIMARY KEY,
 sha256 TEXT NOT NULL UNIQUE,
 original_name TEXT NOT NULL,
 content_type TEXT NOT NULL,
 size_bytes INTEGER NOT NULL CHECK(size_bytes > 0 AND size_bytes <= 20971520),
 original_bytes BLOB NOT NULL,
 document_type TEXT NOT NULL DEFAULT 'unknown',
 counterparty TEXT NOT NULL DEFAULT '',
 document_number TEXT NOT NULL DEFAULT '',
 document_date TEXT,
 note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending',
 version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
 uploaded_by INTEGER NOT NULL REFERENCES users(id),
 uploaded_at TEXT NOT NULL,
 updated_by INTEGER,
 updated_at TEXT,
 fields_json TEXT NOT NULL DEFAULT '{}',
 CHECK(status IN ('pending','review','reviewed')),
 CHECK(document_type IN ('unknown','upd','waybill','invoice','bill','act',
                         'receipt','contract','payment','other'))
);
CREATE TABLE IF NOT EXISTS med_supply_document_events (
 id INTEGER PRIMARY KEY,
 document_id INTEGER NOT NULL REFERENCES med_supply_documents(id),
 action TEXT NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 before_json TEXT,
 after_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_med_supply_documents_status
 ON med_supply_documents(status,id);
CREATE INDEX IF NOT EXISTS ix_med_supply_document_events_document
 ON med_supply_document_events(document_id,id);
"""


class Conflict(ValueError):
    pass


def init_schema(conn):
    """Explicit schema setup only; GET and ordinary app startup do not write."""
    conn.executescript(SCHEMA)


def ready(conn):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='med_supply_documents'"
    ).fetchone()) and bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='med_supply_document_events'"
    ).fetchone())


def _str(value, limit, field):
    value = str(value or "").strip()
    if len(value) > limit or any(ord(c) < 32 and c not in "\t" for c in value):
        raise ValueError(f"{field}: не больше {limit} символов, без управляющих знаков.")
    return value


def _iso_date(value):
    raw = _str(value, 10, "Дата документа")
    if not raw:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError("Укажите дату в формате ГГГГ-ММ-ДД.")
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ValueError("Некорректная дата документа.") from None


def _filename(name):
    # Do not turn names supplied by a browser into filesystem paths, HTML or URLs.
    name = str(name or "").replace("\\", "/")
    name = PurePath(name).name.strip().strip(".")
    if (not name or len(name) > 240
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("Некорректное имя файла (максимум 240 символов).")
    return name


def _zip_kind(raw, ext):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 1000:
                raise ValueError("Некорректный архив документа.")
            if sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                raise ValueError("Слишком большой распакованный документ.")
            if any(i.filename.startswith("/") or ".." in i.filename.split("/")
                   for i in infos):
                raise ValueError("Недопустимые пути внутри архива.")
            names = {i.filename for i in infos}
            if ext == ".docx" and ("word/document.xml" not in names
                                    or "[Content_Types].xml" not in names):
                raise ValueError("Содержимое файла не соответствует DOCX.")
            if ext == ".xlsx" and ("xl/workbook.xml" not in names
                                    or "[Content_Types].xml" not in names):
                raise ValueError("Содержимое файла не соответствует XLSX.")
            if ext in (".odt", ".ods") and "mimetype" not in names:
                raise ValueError("Содержимое файла не соответствует ODF.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Некорректный ZIP/Office-документ.") from exc


def validate_file(raw, filename):
    """Signature-checked document types, never executing or unpacking uploads."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Размер файла должен быть от 1 байта до 20 МБ.")
    name = _filename(filename)
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not ext and raw.startswith(b"%PDF-"):
        ext = ".pdf"  # Some supplier portals strip the extension.
    magic = {
        ".pdf": (raw.startswith(b"%PDF-"), "application/pdf"),
        ".jpg": (raw.startswith(b"\xff\xd8\xff"), "image/jpeg"),
        ".jpeg": (raw.startswith(b"\xff\xd8\xff"), "image/jpeg"),
        ".png": (raw.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
        ".webp": (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP", "image/webp"),
        ".tif": (raw[:4] in (b"II*\x00", b"MM\x00*"), "image/tiff"),
        ".tiff": (raw[:4] in (b"II*\x00", b"MM\x00*"), "image/tiff"),
        ".bmp": (raw.startswith(b"BM"), "image/bmp"),
        ".heic": (raw[4:8] == b"ftyp" and raw[8:12] in
                  (b"heic", b"heix", b"hevc", b"hevx", b"mif1"), "image/heic"),
        ".heif": (raw[4:8] == b"ftyp" and raw[8:12] in
                  (b"heic", b"heix", b"hevc", b"hevx", b"mif1"), "image/heif"),
        ".doc": (raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
                 "application/msword"),
        ".xls": (raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
                 "application/vnd.ms-excel"),
        ".rtf": (raw.startswith(b"{\\rtf"), "application/rtf"),
    }
    if ext in magic:
        allowed, mime = magic[ext]
        if not allowed:
            raise ValueError("Содержимое не соответствует расширению файла.")
        return name, mime
    zipped = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".odt": "application/vnd.oasis.opendocument.text",
        ".ods": "application/vnd.oasis.opendocument.spreadsheet",
        ".zip": "application/zip",
    }
    if ext in zipped:
        if not raw.startswith(b"PK\x03\x04"):
            raise ValueError("Содержимое не соответствует ZIP/Office-файлу.")
        _zip_kind(raw, ext)
        return name, zipped[ext]
    plain = {
        ".xml": "application/xml", ".csv": "text/csv",
        ".txt": "text/plain", ".json": "application/json",
    }
    if ext in plain:
        if b"\x00" in raw:
            raise ValueError("Текстовый документ содержит двоичные данные.")
        if any(b < 32 and b not in (9, 10, 13) for b in raw):
            raise ValueError("Недопустимое содержимое текстового документа.")
        # Supplier exports commonly use UTF-8 or legacy Windows-1251.
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw.decode("cp1251")
        return name, plain[ext]
    raise ValueError(
        "Поддерживаются PDF, изображения, Word, Excel, XML, CSV, TXT, JSON, ODF, RTF и ZIP."
    )


def details(form):
    kind = str(form.get("document_type") or "unknown")
    status = str(form.get("status") or "pending")
    if kind not in TYPE_KEYS or status not in STATUS_KEYS:
        raise ValueError("Выберите допустимый тип документа и статус.")
    return {
        "document_type": kind,
        "status": status,
        "counterparty": _str(form.get("counterparty"), 200, "Контрагент"),
        "document_number": _str(form.get("document_number"), 100, "Номер документа"),
        "document_date": _iso_date(form.get("document_date")),
        "note": _str(form.get("note"), 2000, "Комментарий"),
    }


def _now():
    return datetime.now(timezone.utc).isoformat()


def _event(conn, document_id, action, actor, before, after, now):
    conn.execute(
        "INSERT INTO med_supply_document_events "
        "(document_id,action,actor_id,created_at,before_json,after_json) "
        "VALUES(?,?,?,?,?,?)",
        (document_id, action, actor, now,
         json.dumps(before, ensure_ascii=False, sort_keys=True) if before else None,
         json.dumps(after, ensure_ascii=False, sort_keys=True)),
    )


def save_upload(conn, raw, filename, form, actor):
    """Atomic original + metadata + history; same SHA cannot be saved twice."""
    name, mime = validate_file(raw, filename)
    info = details(form)
    sha = hashlib.sha256(raw).hexdigest()
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        previous = conn.execute(
            "SELECT id FROM med_supply_documents WHERE sha256=?", (sha,)
        ).fetchone()
        if previous:
            conn.rollback()
            return previous["id"], False
        cursor = conn.execute(
            "INSERT INTO med_supply_documents "
            "(sha256,original_name,content_type,size_bytes,original_bytes,"
            "document_type,counterparty,document_number,document_date,note,"
            "status,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sha, name, mime, len(raw), sqlite3.Binary(raw),
             info["document_type"], info["counterparty"],
             info["document_number"], info["document_date"], info["note"],
             info["status"], actor, now),
        )
        document_id = cursor.lastrowid
        _event(conn, document_id, "upload", actor, None, {
            **info, "original_name": name, "sha256": sha, "size_bytes": len(raw),
            "content_type": mime,
        }, now)
        conn.commit()
        return document_id, True
    except BaseException:
        conn.rollback()
        raise


def update_details(conn, doc_id, form, actor):
    info = details(form)
    raw_version = str(form.get("version") or "")
    if not re.fullmatch(r"\d{1,12}", raw_version):
        raise ValueError("Некорректная версия карточки.")
    version = int(raw_version)
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        old = conn.execute(
            "SELECT id,document_type,counterparty,document_number,"
            "document_date,note,status,version FROM med_supply_documents "
            "WHERE id=?", (doc_id,),
        ).fetchone()
        if not old:
            raise LookupError("Документ не найден.")
        if old["version"] != version:
            raise Conflict("Карточка уже изменена. Обновите страницу.")
        before = dict(old)
        if any(old[k] != value for k, value in info.items()):
            conn.execute(
                "UPDATE med_supply_documents SET document_type=?,counterparty=?,"
                "document_number=?,document_date=?,note=?,status=?,"
                "version=version+1,updated_by=?,updated_at=? WHERE id=?",
                (info["document_type"], info["counterparty"],
                 info["document_number"], info["document_date"],
                 info["note"], info["status"], actor, now, doc_id),
            )
            _event(conn, doc_id, "review", actor, before, {
                **info, "version": version + 1,
            }, now)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def list_documents(conn, q="", status="", kind="", page=1):
    """No original_bytes in listing; search both Cyrillic and Latin casefolded."""
    if status and status not in STATUS_KEYS:
        raise ValueError("Неизвестный фильтр статуса.")
    if kind and kind not in TYPE_KEYS:
        raise ValueError("Неизвестный фильтр типа.")
    q = _str(q, 160, "Поиск").casefold()
    page = max(1, min(int(page), 10000))
    rows = [
        dict(r) for r in conn.execute(
            "SELECT id,original_name,content_type,size_bytes,document_type,"
            "counterparty,document_number,document_date,note,status,version,"
            "uploaded_at,uploaded_by FROM med_supply_documents ORDER BY id DESC"
        )
    ]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if kind:
        rows = [r for r in rows if r["document_type"] == kind]
    if q:
        rows = [r for r in rows if q in " ".join(
            str(r[k] or "") for k in
            ("original_name", "counterparty", "document_number", "note")
        ).casefold()]
    return rows[(page-1)*MAX_RESULTS:page*MAX_RESULTS], len(rows)


def _document(conn, doc_id):
    return conn.execute(
        "SELECT id,original_name,content_type,size_bytes,document_type,"
        "counterparty,document_number,document_date,note,status,version,"
        "uploaded_at,uploaded_by,updated_at FROM med_supply_documents WHERE id=?",
        (doc_id,),
    ).fetchone()


def register(app, db, permission_required, require_csrf, csrf_token, user_permissions):
    def can_manage():
        permissions = user_permissions(g.user)
        return bool(permissions & {
            "medical_supplies_edit", "medical_supplies_documents_manage",
        })

    def page(error=None, status_code=200):
        conn = db()
        if not ready(conn):
            return render_template(
                "medical_supplies_documents.html",
                title="Входящие документы — Медрасходка",
                setup_pending=True, documents=[], selected=None, can_manage=False,
            ), 503
        try:
            q = request.args.get("q", "")
            filter_status = request.args.get("status", "")
            kind = request.args.get("type", "")
            page_num = request.args.get("page", "1")
            if not re.fullmatch(r"\d{1,5}", page_num):
                raise ValueError("Неверная страница.")
            rows, count = list_documents(conn, q, filter_status, kind, int(page_num))
            chosen = request.args.get("id", "")
            if chosen and not re.fullmatch(r"\d{1,12}", chosen):
                abort(404)
            selected = _document(conn, int(chosen)) if chosen else None
            if chosen and not selected:
                abort(404)
            events = []
            if selected:
                events = [
                    dict(r) for r in conn.execute(
                        "SELECT action,created_at,actor_id,before_json,after_json "
                        "FROM med_supply_document_events WHERE document_id=? "
                        "ORDER BY id DESC LIMIT 30", (selected["id"],),
                    )
                ]
            return render_template(
                "medical_supplies_documents.html",
                title="Входящие документы — Медрасходка",
                setup_pending=False, documents=rows, selected=selected,
                events=events, count=count, page_num=int(page_num),
                can_manage=can_manage(), csrf=csrf_token(), error=error,
                types=TYPES, statuses=STATUSES, q=q,
                filter_status=filter_status, filter_type=kind,
                uploaded=request.args.get("uploaded") == "1",
                duplicate=request.args.get("duplicate") == "1",
                updated=request.args.get("updated") == "1",
            ), status_code
        except ValueError as exc:
            return str(exc), 400

    @app.get("/accounting/medical-supplies/incoming/")
    @permission_required("accounting")
    def medical_supplies_inbox():
        return page()

    @app.post("/accounting/medical-supplies/incoming/upload")
    @permission_required("accounting")
    def medical_supplies_inbox_upload():
        if not can_manage():
            abort(403)
        require_csrf()
        if not ready(db()):
            abort(503)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return page("Выберите документ для загрузки.", 400)
        try:
            # Existing app MAX_CONTENT_LENGTH=25 MB; per-file cap is 20 MB.
            raw = upload.stream.read(MAX_BYTES + 1)
            doc_id, created = save_upload(
                db(), raw, upload.filename, request.form, g.user["id"]
            )
        except ValueError as exc:
            return page(str(exc), 400)
        return redirect(
            url_for("medical_supplies_inbox", id=doc_id,
                    uploaded="1" if created else None,
                    duplicate="1" if not created else None),
            code=303,
        )

    @app.post("/accounting/medical-supplies/incoming/<int:doc_id>/review")
    @permission_required("accounting")
    def medical_supplies_inbox_review(doc_id):
        if not can_manage():
            abort(403)
        require_csrf()
        if not ready(db()):
            abort(503)
        try:
            update_details(db(), doc_id, request.form, g.user["id"])
        except LookupError:
            abort(404)
        except Conflict as exc:
            return page(str(exc), 409)
        except ValueError as exc:
            return page(str(exc), 400)
        return redirect(url_for("medical_supplies_inbox", id=doc_id, updated=1),
                        code=303)

    @app.get("/accounting/medical-supplies/incoming/<int:doc_id>/download")
    @permission_required("accounting")
    def medical_supplies_inbox_download(doc_id):
        if not ready(db()):
            abort(503)
        row = db().execute(
            "SELECT original_name,original_bytes FROM med_supply_documents "
            "WHERE id=?", (doc_id,),
        ).fetchone()
        if not row:
            abort(404)
        # Always force download: never render HTML/CSV/Office bytes as site UI.
        response = send_file(
            io.BytesIO(row["original_bytes"]),
            mimetype="application/octet-stream", as_attachment=True,
            download_name=row["original_name"],
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    @app.get("/accounting/medical-supplies/incoming/<int:doc_id>/view")
    @permission_required("accounting")
    def medical_supplies_inbox_view(doc_id):
        if not ready(db()):
            abort(503)
        row = db().execute(
            "SELECT original_name,content_type,original_bytes "
            "FROM med_supply_documents WHERE id=?", (doc_id,),
        ).fetchone()
        if not row:
            abort(404)
        # In-browser display is limited to passive PDF/images, not Office/XML.
        if row["content_type"] not in {
            "application/pdf", "image/jpeg", "image/png",
            "image/webp", "image/tiff", "image/bmp",
        }:
            abort(415)
        response = send_file(
            io.BytesIO(row["original_bytes"]), mimetype=row["content_type"],
            as_attachment=False,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        return response
