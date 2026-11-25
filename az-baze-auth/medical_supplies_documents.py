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
import zlib
from xml.parsers import expat
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
    # A reviewer may write multiple lines in notes; names and references
    # must remain single-line and free from control characters.
    allowed = "\t\r\n" if field == "Комментарий" else ""
    if len(value) > limit or any(ord(c) < 32 and c not in allowed for c in value):
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


ZIP_MAX_MEMBERS = 1000
ZIP_MAX_UNCOMPRESSED = 100 * 1024 * 1024
ZIP_MAX_MEMBER = 32 * 1024 * 1024
ZIP_MAX_METADATA = 2 * 1024 * 1024
ZIP_CHUNK = 64 * 1024
OOXML_CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
OOXML_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OOXML_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"


def _zip_safe_name(name):
    """Accept harmless Windows separators, reject any absolute/traversing path.

    Validate paths without extracting files. The same canonical comparison is
    used to reject duplicate members, case and separator aliases across OSes.
    """
    if not name or len(name) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Недопустимое имя файла внутри архива.")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Абсолютный путь или диск Windows внутри архива.")
    # Reject dot navigation, alternate data streams, empty components and
    # colon-based drive escapes on any OS. Directory trailing / is permitted.
    parts = normalized.rstrip("/").split("/")
    if any(p in ("", ".", "..") or ":" in p or p.endswith((" ", "."))
           for p in parts):
        raise ValueError("Недопустимый относительный путь внутри архива.")
    for segment in parts:
        # Win32 device names and trailing extensions must not be valid archive
        # paths in a portable inbox (even though originals are not extracted).
        stem = segment.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
            r"(COM|LPT)[1-9]", stem
        ):
            raise ValueError("Недопустимое имя устройства Windows в архиве.")
    return normalized


def _zip_member_xml(archive, info, expected_root, *, inspect=None):
    """Bounded streaming XML validation with DTD/entity rejection, no XML tree."""
    parser = expat.ParserCreate(namespace_separator="}")
    state = {"root": None}
    def start(name, attrs):
        if state["root"] is None:
            state["root"] = name
        if inspect:
            inspect(name, attrs)
    def reject(*_args):
        raise ValueError("DTD и пользовательские сущности запрещены в Office XML.")
    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = lambda *_args: 0
    seen = 0
    try:
        with archive.open(info, "r") as reader:
            while True:
                chunk = reader.read(ZIP_CHUNK)
                if not chunk:
                    break
                seen += len(chunk)
                if seen > ZIP_MAX_UNCOMPRESSED:
                    raise ValueError("Превышен лимит разбора Office XML.")
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (expat.ExpatError, UnicodeError) as exc:
        raise ValueError("Повреждённая структура Office XML.") from exc
    if state["root"] != expected_root:
        raise ValueError("Тип внутреннего Office XML не соответствует формату.")


def _zip_kind(raw, ext):
    """Check portable paths, full member CRCs and actual Office structure.

    Every compressed member is streamed to EOF (CRC is then checked by Python's
    zipfile reader). No member is extracted and no decompressed bytes are
    retained. Total uncompressed work and member count are strictly bounded.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > ZIP_MAX_MEMBERS:
                raise ValueError("Некорректный архив документа.")
            if sum(i.file_size for i in infos) > ZIP_MAX_UNCOMPRESSED:
                raise ValueError("Слишком большой распакованный документ.")
            names = {}
            for info in infos:
                normalized = _zip_safe_name(info.filename)
                alias = normalized.casefold()
                if alias in names:
                    raise ValueError("Повторяющиеся имена внутри архива.")
                names[alias] = info
                if info.flag_bits & 1:
                    raise ValueError("Зашифрованные ZIP-файлы не поддерживаются.")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("Недопустимый способ сжатия ZIP.")
                if info.file_size > ZIP_MAX_MEMBER:
                    raise ValueError("Слишком большой файл внутри ZIP.")
                if info.file_size and info.compress_size == 0:
                    raise ValueError("Повреждённые размеры ZIP.")
                if info.compress_size and info.file_size > info.compress_size * 1000:
                    raise ValueError("Слишком высокая степень распаковки ZIP.")
                # Symlinks and platform-specific special files do not belong
                # in a document inbox. ZIP directories are harmless.
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in (0, 0o100000, 0o040000):
                    raise ValueError("Специальные файлы запрещены в ZIP.")
                if info.is_dir() and info.file_size:
                    raise ValueError("Каталог ZIP содержит данные.")

            # Fully read every member in bounded chunks: CRC is checked when
            # the last chunk is consumed, not by listing names or headers.
            total = 0
            for info in infos:
                size = 0
                with archive.open(info, "r") as member:
                    while True:
                        chunk = member.read(ZIP_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if size > ZIP_MAX_MEMBER or total > ZIP_MAX_UNCOMPRESSED:
                            raise ValueError("Превышен лимит распаковки ZIP.")
                if size != info.file_size:
                    raise ValueError("Размер файла внутри ZIP не совпадает.")

            if ext not in (".docx", ".xlsx", ".odt", ".ods"):
                return
            if ext in (".docx", ".xlsx"):
                kind = "word/document.xml" if ext == ".docx" else "xl/workbook.xml"
                expected_content = (
                    "application/vnd.openxmlformats-officedocument."
                    + ("wordprocessingml.document.main+xml" if ext == ".docx"
                       else "spreadsheetml.sheet.main+xml")
                )
                if not {"[content_types].xml", "_rels/.rels", kind} <= set(names):
                    raise ValueError("В Office-документе отсутствуют обязательные части.")
                if any(names[n].file_size > ZIP_MAX_METADATA
                       for n in ("[content_types].xml", "_rels/.rels")):
                    raise ValueError("Слишком большие метаданные Office.")

                found = {"override": False, "relationship": False}
                def content_item(name, attrs):
                    if name == f"{OOXML_CONTENT_NS}}}Override":
                        if (attrs.get("PartName") == "/" + kind
                                and attrs.get("ContentType") == expected_content):
                            found["override"] = True
                def root_relation(name, attrs):
                    if name == f"{OOXML_RELS_NS}}}Relationship":
                        target = str(attrs.get("Target") or "").replace("\\", "/").lstrip("/")
                        if (attrs.get("Type") == OOXML_OFFICE_REL
                                and attrs.get("TargetMode", "Internal") == "Internal"
                                and target == kind):
                            found["relationship"] = True
                _zip_member_xml(archive, names["[content_types].xml"],
                                f"{OOXML_CONTENT_NS}}}Types", inspect=content_item)
                _zip_member_xml(archive, names["_rels/.rels"],
                                f"{OOXML_RELS_NS}}}Relationships", inspect=root_relation)
                expected_main_root = (
                    "http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                    if ext == ".docx" else
                    "http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook"
                )
                _zip_member_xml(archive, names[kind], expected_main_root)
                if not all(found.values()):
                    raise ValueError("Тип и связи Office-документа не подтверждены.")
            else:
                if not {"mimetype", "content.xml", "meta-inf/manifest.xml"} <= set(names):
                    raise ValueError("В ODF отсутствуют обязательные части.")
                if names["mimetype"].file_size > 200:
                    raise ValueError("Некорректный MIME внутри ODF.")
                expected_mime = (
                    b"application/vnd.oasis.opendocument.text"
                    if ext == ".odt" else
                    b"application/vnd.oasis.opendocument.spreadsheet"
                )
                if archive.read(names["mimetype"]) != expected_mime:
                    raise ValueError("Тип ODF не соответствует расширению.")
                _zip_member_xml(archive, names["content.xml"],
                                "urn:oasis:names:tc:opendocument:xmlns:office:1.0}document-content")
                _zip_member_xml(archive, names["meta-inf/manifest.xml"],
                                "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}manifest")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError,
            EOFError, NotImplementedError, ValueError, zlib.error, expat.ExpatError) as exc:
        raise ValueError("Небезопасный или повреждённый ZIP/Office-документ: " + str(exc)) from exc


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
    # Upload always enters the review queue; a forged hidden status field
    # cannot mark a newly received document as already reviewed.
    info["status"] = "pending"
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

    # The review extension owns only OCR drafts in the existing document.
    # It never registers inventory or finance writes.
    import medical_supplies_ocr
    medical_supplies_ocr.register(app, db, permission_required, require_csrf,
                                  csrf_token, can_manage)
