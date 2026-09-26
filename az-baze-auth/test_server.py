import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

if os.environ.get("AZBAZE_TEST_CONTOUR") != "1":
    raise RuntimeError("test_server.py may run only with AZBAZE_TEST_CONTOUR=1")

TEST_STATE_ROOT = Path(os.environ.get("AZBAZE_TEST_STATE_ROOT", "")).resolve()
TEST_DB = Path(os.environ.get("AZBAZE_DB", "")).resolve()
TEST_SITE_ROOT = Path(os.environ.get("AZBAZE_SITE_ROOT", "")).resolve()
TEST_CREDENTIALS_KEY = Path(os.environ.get("AZBAZE_CREDENTIALS_KEY", "")).resolve()
TEST_FINREZ_DATA = Path(os.environ.get("AZ_FINREZ_DATA_PATH", "")).resolve()
TEST_FINREZ_KEY = Path(os.environ.get("AZ_FINREZ_KEY_PATH", "")).resolve()

EXPECTED_STATE_ROOT = Path("/var/lib/az-baze-test")
EXPECTED_SITE_ROOT = Path("/var/www/az-baze-test")

if TEST_STATE_ROOT != EXPECTED_STATE_ROOT:
    raise RuntimeError(f"unsafe test state root: {TEST_STATE_ROOT}")
if TEST_SITE_ROOT != EXPECTED_SITE_ROOT:
    raise RuntimeError(f"unsafe test site root: {TEST_SITE_ROOT}")

for path in (TEST_DB, TEST_CREDENTIALS_KEY, TEST_FINREZ_DATA, TEST_FINREZ_KEY):
    try:
        path.relative_to(TEST_STATE_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"test path escapes isolated state root: {path}") from exc

from server import app  # noqa: E402
import contact_excel_import  # noqa: E402
import economics_control  # noqa: E402


app.config.update(
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_NAME="az_baze_private_test_session",
)


economics_control.DATA_PATH = TEST_STATE_ROOT / "economics-control.json"


def _test_contact_backup_database(db_path):
    source = Path(db_path).resolve()
    try:
        source.relative_to(TEST_STATE_ROOT)
    except ValueError as exc:
        raise RuntimeError("private test contact backup refused non-test DB") from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = TEST_STATE_ROOT / "contact-import-backups" / f"contacts-excel-{stamp}"
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    destination = directory / "auth-before-contact-import.db"

    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    os.chmod(destination, 0o600)
    check_conn = sqlite3.connect(destination)
    try:
        if check_conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("private test contact backup failed integrity check")
    finally:
        check_conn.close()
    return str(directory)


contact_excel_import.backup_database = _test_contact_backup_database


PRIVATE_TEST_BANNER = (
    '<div id="az-private-test-banner" '
    'style="position:sticky;top:0;z-index:50000;padding:10px 14px;'
    'background:#7b6038;color:#fff;text-align:center;'
    'font:700 12px Montserrat,Arial,sans-serif;letter-spacing:.03em">'
    'ПРИВАТНЫЙ TEST-КОНТУР — изменения не влияют на рабочую «Архитектуру здоровья»'
    "</div>"
)


@app.after_request
def private_test_markers(response):
    response.headers["X-AZ-BAZE-Contour"] = "private-test"
    if response.mimetype != "text/html":
        return response
    html = response.get_data(as_text=True)
    if "az-private-test-banner" in html:
        return response
    lower = html.lower()
    body_index = lower.find("<body")
    if body_index < 0:
        return response
    body_close = lower.find(">", body_index)
    if body_close < 0:
        return response
    html = html[: body_close + 1] + PRIVATE_TEST_BANNER + html[body_close + 1 :]
    response.set_data(html)
    return response
