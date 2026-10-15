import hashlib
import mimetypes
import os
from pathlib import Path

from flask import abort, g, jsonify, render_template, request, send_file

from app import admin_required, db, iso_now, permission_required, require_csrf


KNOWLEDGE_ROOT = Path(os.environ.get("AZBAZE_KNOWLEDGE_ROOT", "/mnt/qnap-obuchenie"))
KNOWLEDGE_TEST_ROOT = Path(os.environ.get("AZBAZE_KNOWLEDGE_TEST_ROOT", "/var/lib/az-baze/knowledge-test"))
HIDDEN_TOP_LEVEL = {"@Recycle", "Скляров"}
HIDDEN_EXTENSIONS = {
    ".lrf", ".загружено", ".yandex_disk_download",
    ".ini", ".ini[1]", ".css", ".dat", ".exe",
}
SPECIALIZED_EXTENSIONS = {".dcm", ".stl", ".dentalcad"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".mpeg", ".mts"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".fb2"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    parent_path TEXT NOT NULL,
    name TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK(entry_type IN ('directory','file')),
    extension TEXT NOT NULL DEFAULT '',
    media_kind TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_parent
    ON knowledge_entries(parent_path, hidden, name);

CREATE TABLE IF NOT EXISTS knowledge_policies (
    relative_path TEXT PRIMARY KEY,
    allow_download INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by INTEGER
);
"""


def init_schema(conn):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _stable_id(relative_path):
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:32]


def _kind(extension):
    ext = extension.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    return "other"


def _hidden(relative_path, entry_type):
    path = Path(relative_path)
    parts = path.parts
    if parts and parts[0] in HIDDEN_TOP_LEVEL:
        return True
    name = path.name
    if name.startswith("._"):
        return True
    if entry_type == "file" and path.suffix.lower() in HIDDEN_EXTENSIONS:
        return True
    if entry_type == "file" and path.suffix.lower() in SPECIALIZED_EXTENSIONS:
        return True
    if entry_type == "file" and _kind(path.suffix.lower()) == "other":
        return True
    return False


def _safe_root_path(relative_path):
    root = KNOWLEDGE_ROOT.resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        abort(404)
    return target


def _test_media_path(entry_id):
    if not entry_id or any(ch not in "0123456789abcdef" for ch in entry_id.lower()):
        abort(404)
    return KNOWLEDGE_TEST_ROOT / f"{entry_id}.mp4"


def _scan_rows():
    if not KNOWLEDGE_ROOT.is_dir():
        raise RuntimeError(f"QNAP knowledge root is unavailable: {KNOWLEDGE_ROOT}")

    indexed_at = iso_now()
    file_rows = []
    directory_paths = set()

    for current, dirs, files in os.walk(KNOWLEDGE_ROOT):
        current_path = Path(current)
        rel_current = current_path.relative_to(KNOWLEDGE_ROOT)

        # Legacy top-level areas are not part of the knowledge catalog.
        # A nested folder may have the same word in its name and must remain available.
        if str(rel_current) == ".":
            dirs[:] = [
                name for name in dirs
                if name not in HIDDEN_TOP_LEVEL and not name.startswith("._")
            ]

        for name in files:
            relative_path = rel_current / name
            relative = str(relative_path.as_posix())

            # Reject technical/runtime/specialized files before stat().
            # This keeps large software bundles from bloating the catalog and
            # avoids unnecessary SMB metadata calls for unsupported files.
            if _hidden(relative, "file"):
                continue

            path = current_path / name
            try:
                stat = path.stat()
            except OSError:
                continue

            ext = path.suffix.lower()
            parent = relative_path.parent
            parent_text = "" if str(parent) == "." else str(parent.as_posix())

            file_rows.append((
                _stable_id(relative), relative, parent_text,
                name, "file", ext, _kind(ext), int(stat.st_size),
                str(int(stat.st_mtime)), 0, indexed_at,
            ))

            # Only directories leading to at least one supported material are
            # indexed. Empty software/runtime directory trees stay invisible.
            while str(parent) not in ("", "."):
                parent_relative = str(parent.as_posix())
                if _hidden(parent_relative, "directory"):
                    break
                directory_paths.add(parent_relative)
                parent = parent.parent

    directory_rows = []
    for relative in sorted(
        directory_paths,
        key=lambda value: (value.count("/"), value.casefold()),
    ):
        path = Path(relative)
        parent = path.parent
        parent_text = "" if str(parent) == "." else str(parent.as_posix())
        directory_rows.append((
            _stable_id(relative), relative, parent_text,
            path.name, "directory", "", "directory", 0, None,
            0, indexed_at,
        ))

    return directory_rows + file_rows


def reindex(conn):
    init_schema(conn)
    rows = _scan_rows()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM knowledge_entries")
        conn.executemany(
            """
            INSERT INTO knowledge_entries(
                id,relative_path,parent_path,name,entry_type,extension,media_kind,
                size_bytes,modified_at,hidden,indexed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def _entry(conn, entry_id):
    init_schema(conn)
    return conn.execute(
        "SELECT * FROM knowledge_entries WHERE id=? AND hidden=0",
        (entry_id,),
    ).fetchone()


def _policy(conn, relative_path):
    init_schema(conn)
    row = conn.execute(
        "SELECT allow_download FROM knowledge_policies WHERE relative_path=?",
        (relative_path,),
    ).fetchone()
    return bool(row[0]) if row else False


def register(app):
    @app.get("/api/knowledge/catalog")
    @permission_required("knowledge")
    def knowledge_catalog_api():
        conn = db()
        init_schema(conn)
        parent = request.args.get("path", "").strip().strip("/")
        rows = conn.execute(
            """
            SELECT id,relative_path,parent_path,name,entry_type,extension,media_kind,
                   size_bytes,modified_at
            FROM knowledge_entries
            WHERE parent_path=? AND hidden=0
            ORDER BY entry_type DESC, name COLLATE NOCASE
            """,
            (parent,),
        ).fetchall()
        return jsonify(
            path=parent,
            entries=[dict(row) for row in rows],
            root_available=KNOWLEDGE_ROOT.is_dir(),
        )

    @app.get("/api/knowledge/status")
    @permission_required("knowledge")
    def knowledge_status_api():
        conn = db()
        init_schema(conn)
        total = conn.execute(
            "SELECT COUNT(*) FROM knowledge_entries WHERE hidden=0"
        ).fetchone()[0]
        files = conn.execute(
            "SELECT COUNT(*) FROM knowledge_entries WHERE hidden=0 AND entry_type='file'"
        ).fetchone()[0]
        return jsonify(
            root=str(KNOWLEDGE_ROOT),
            root_available=KNOWLEDGE_ROOT.is_dir(),
            entries=total,
            files=files,
        )

    @app.post("/api/knowledge/reindex")
    @admin_required
    def knowledge_reindex_api():
        require_csrf()
        try:
            count = reindex(db())
        except RuntimeError as exc:
            return jsonify(status="error", message=str(exc)), 503
        return jsonify(status="ok", entries=count)

    @app.get("/knowledge-library/")
    @permission_required("knowledge")
    def knowledge_library():
        conn = db()
        init_schema(conn)
        parent = request.args.get("path", "").strip().strip("/")
        rows = conn.execute(
            """
            SELECT id,relative_path,parent_path,name,entry_type,extension,media_kind,
                   size_bytes,modified_at
            FROM knowledge_entries
            WHERE parent_path=? AND hidden=0
            ORDER BY entry_type DESC, name COLLATE NOCASE
            """,
            (parent,),
        ).fetchall()
        parent_path = ""
        if parent:
            parent_path = str(Path(parent).parent.as_posix())
            if parent_path == ".":
                parent_path = ""
        return render_template(
            "knowledge_library.html",
            current_path=parent,
            parent_path=parent_path,
            entries=rows,
            root_available=KNOWLEDGE_ROOT.is_dir(),
        )

    @app.get("/knowledge-view/<entry_id>")
    @permission_required("knowledge")
    def knowledge_view(entry_id):
        conn = db()
        row = _entry(conn, entry_id)
        if not row or row["entry_type"] != "file":
            abort(404)
        media_mimetype = mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
        return render_template(
            "knowledge_view.html",
            entry=row,
            allow_download=_policy(conn, row["relative_path"]),
            media_mimetype=media_mimetype,
            media_url=f"/knowledge-media/{entry_id}",
            test_mode=False,
        )

    @app.get("/knowledge-test/<entry_id>")
    @permission_required("knowledge")
    def knowledge_test(entry_id):
        conn = db()
        row = _entry(conn, entry_id)
        if not row or row["entry_type"] != "file" or row["media_kind"] != "video":
            abort(404)
        path = _test_media_path(entry_id)
        if not path.is_file():
            abort(404)
        return render_template(
            "knowledge_view.html",
            entry=row,
            allow_download=False,
            media_mimetype="video/mp4",
            media_url=f"/knowledge-test-media/{entry_id}",
            test_mode=True,
        )

    @app.get("/knowledge-test-media/<entry_id>")
    @permission_required("knowledge")
    def knowledge_test_media(entry_id):
        conn = db()
        row = _entry(conn, entry_id)
        if not row or row["entry_type"] != "file" or row["media_kind"] != "video":
            abort(404)
        path = _test_media_path(entry_id)
        if not path.is_file():
            abort(404)
        response = send_file(
            path,
            mimetype="video/mp4",
            as_attachment=False,
            download_name=f"{entry_id}.mp4",
            conditional=True,
            etag=True,
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/knowledge-media/<entry_id>")
    @permission_required("knowledge")
    def knowledge_media(entry_id):
        conn = db()
        row = _entry(conn, entry_id)
        if not row or row["entry_type"] != "file":
            abort(404)
        path = _safe_root_path(row["relative_path"])
        if not path.is_file():
            abort(404)
        response = send_file(
            path,
            mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            as_attachment=False,
            download_name=row["name"],
            conditional=True,
            etag=True,
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/knowledge-download/<entry_id>")
    @permission_required("knowledge")
    def knowledge_download(entry_id):
        conn = db()
        row = _entry(conn, entry_id)
        if not row or row["entry_type"] != "file":
            abort(404)
        if not _policy(conn, row["relative_path"]):
            abort(403)
        path = _safe_root_path(row["relative_path"])
        if not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, download_name=row["name"], conditional=True)
