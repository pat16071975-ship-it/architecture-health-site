#!/usr/bin/env python3
"""Install exact audited PR273 code and two pinned wheels; never restore DB.

The owner authorized merge/deploy of PR273 and the two dependencies.
Default is preflight. All network and pip work happens in a private copy of
the virtualenv before service stop. Rollback restores the whole old venv.
No OCR execution, migrations, original uploads, stock or financial imports.
"""
import argparse
import base64
import csv
import fcntl
import grp
import hashlib
import io
import json
import os
import pwd
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = "pat16071975-ship-it/architecture-health-site"
BASE = "52936debf785053d1ba3d4d7e24f7366890e9a05"
AUDITED = "ec58a4dded9ea6af45d84d5373370425a180cf6d"
MERGED = "e30b84c374da87013cc4f7089558cfeeba68c206"
TREE = "74eb5d06ca955ef4deaf74c2a318799b7675621f"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
LOCK = Path("/run/lock/az-baze-pr273.lock")
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
FILES = {
    "medical_supplies_ocr.py": (
        "62540d41905934b30a69c2082412393ffda2fb8c",
        "23d9789b08166c45f0d5ce4704b82d68427cd071"),
    "medical_supplies_ocr_image.py": (None, "0fb65eaf55ec9f6d69e13cbd8d527c5a51abcadc"),
    "medical_supplies_ocr_enrichment.py": (None, "7304d9db500725951fe9dc75a221e66e729fc53c"),
    "medical_supplies_ocr_quality.py": (None, "c8d0b7b2df97f974d28b408be7c0dc43d1f95d22"),
    "templates/medical_supplies_ocr.html": (
        "26ec122746c92376fdc3b9611df880555f772b52",
        "b449de82048c0e4e45e3b4322c45bf3d55fa3404"),
    "requirements.txt": (
        "8b71edd4ad0e9cf54ac4673c5f2487cc8adc76ab",
        "8607792ae81956fb5d22cbd01b1addb32abe9648"),
}
DEPENDENCIES = {"numpy": "2.2.6", "opencv-python-headless": "4.12.0.88"}
PROTECTED = (
    "app.py", "server.py", "medical_supplies_documents.py",
    "templates/medical_supplies_documents.html",
    "mis_source_archive.py", "mis_archive_bundle.py", "upload_reconcile.py",
    "ident_import.py", "cash_upload.py", "completed_upload.py", "daily_upload.py",
    "paid_services_upload.py", "services_upload.py", "management_lab.py",
)


def require(condition, marker):
    if not condition:
        raise RuntimeError(marker)


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def regular(path):
    require(not path.is_symlink() and path.is_file(), "NOT_REGULAR: " + str(path))


def digest(path):
    regular(path)
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_blob(path, expected, marker):
    regular(path)
    require(blob(path.read_bytes()) == expected, marker + ": " + str(path))


def file_state(path):
    if not path.exists() and not path.is_symlink():
        return None
    regular(path)
    return blob(path.read_bytes())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def canonical_directory(path):
    require(path.is_dir() and not path.is_symlink() and path.resolve() == path,
            "DIRECTORY_NOT_CANONICAL: " + str(path))


def venv_signature(path):
    """Hash dependency bytes, links and permissions, excluding runtime caches."""
    canonical_directory(path)
    result = hashlib.sha256()
    for directory, directories, filenames in os.walk(path, followlinks=False):
        directories[:] = sorted(name for name in directories if name != "__pycache__")
        for name in sorted([*directories, *filenames]):
            entry = Path(directory) / name
            info = entry.lstat()
            relative = str(entry.relative_to(path))
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                record = [relative, "link", mode, os.readlink(entry)]
            elif stat.S_ISREG(info.st_mode):
                if entry.suffix in (".pyc", ".pyo"):
                    continue
                record = [relative, "file", mode, digest(entry)]
            else:
                require(stat.S_ISDIR(info.st_mode), "VENV_SPECIAL_FILE")
                record = [relative, "dir", mode]
            result.update(json.dumps(record, ensure_ascii=True).encode() + b"\n")
    return result.hexdigest()


def isolated_env(stage):
    temporary = stage / "tmp"
    temporary.mkdir(mode=0o700, exist_ok=True)
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "TMPDIR": str(temporary), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}


def isolated_run(args, stage, marker, timeout=120, user=None):
    options = {"env": isolated_env(stage), "cwd": str(stage), "capture_output": True,
               "text": True, "check": False, "timeout": timeout, "umask": 0o022}
    if user is not None:
        options.update(user=user.pw_uid, group=user.pw_gid, extra_groups=[])
    result = subprocess.run(args, **options)
    require(result.returncode == 0, marker)
    return result.stdout.strip()


VENV_PROBE = r'''
import importlib.metadata as metadata,json,os,site,sys
from pathlib import Path
expected=Path(sys.argv[1]).resolve()
assert Path(sys.prefix).resolve()==expected and sys.prefix!=sys.base_prefix
paths=[Path(p).resolve() for p in site.getsitepackages()]
assert paths and all(p.is_relative_to(expected) for p in paths)
packages={}
for distribution in metadata.distributions():
    name=distribution.metadata.get('Name','').lower().replace('_','-').replace('.','-')
    assert name and name not in packages
    packages[name]=distribution.version
result={'packages':packages,'site_packages':[str(p) for p in paths],
        'python':sys.version.split()[0],'prefix':str(expected)}
if len(sys.argv)>2:
    import numpy,cv2
    assert metadata.version('numpy')=='2.2.6'
    assert metadata.version('opencv-python-headless')=='4.12.0.88'
    assert Path(numpy.__file__).resolve().is_relative_to(expected)
    assert Path(cv2.__file__).resolve().is_relative_to(expected)
    image=numpy.zeros((12,12),dtype=numpy.uint8)
    assert cv2.threshold(image,127,255,cv2.THRESH_BINARY)[1].shape==(12,12)
    result['dependency_imports']='PASS'
print(json.dumps(result,sort_keys=True))
'''


def probe_venv(path, stage, dependencies=False, user=None):
    args = [str(path / "bin/python"), "-I", "-B", "-c", VENV_PROBE, str(path)]
    if dependencies:
        args.append("dependencies")
    output = isolated_run(args, stage, "DEPENDENCY_IMPORT_OR_PREFIX_FAILED", 45, user)
    result = json.loads(output)
    require(isinstance(result.get("packages"), dict), "DEPENDENCY_INVENTORY_FAILED")
    return result


def service_user():
    name = command("systemctl", "show", "-p", "User", "--value", SERVICE)
    require(bool(name), "SERVICE_MUST_BE_NONROOT")
    user = pwd.getpwuid(int(name)) if name.isdigit() else pwd.getpwnam(name)
    require(user.pw_uid != 0, "SERVICE_MUST_BE_NONROOT")
    group = command("systemctl", "show", "-p", "Group", "--value", SERVICE)
    gid = (int(group) if group.isdigit() else grp.getgrnam(group).gr_gid) if group else user.pw_gid
    return SimpleNamespace(pw_uid=user.pw_uid, pw_gid=gid, pw_name=user.pw_name)


def verified_wheel_hashes(wheel_paths):
    """Verify the selected platform wheels against the official exact release."""
    actual = {path.name: digest(path) for path in wheel_paths}
    matched = set()
    for name, version in DEPENDENCIES.items():
        request = urllib.request.Request(f"https://pypi.org/pypi/{name}/{version}/json",
            headers={"User-Agent": "AZ-BAZE-PR273-Release"})
        data = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    require(response.status == 200, "PYPI_METADATA_STATUS_FAILED")
                    data = response.read(4 * 1024 * 1024 + 1)
                break
            except (OSError, urllib.error.URLError):
                if attempt:
                    raise RuntimeError("PYPI_METADATA_DOWNLOAD_FAILED") from None
                time.sleep(1)
        require(data is not None and len(data) <= 4 * 1024 * 1024, "PYPI_METADATA_LIMIT")
        releases = json.loads(data).get("urls", [])
        selected = [record for record in releases if record.get("filename") in actual
                    and record.get("packagetype") == "bdist_wheel"]
        require(len(selected) == 1, "PYPI_EXACT_WHEEL_NOT_FOUND")
        record = selected[0]
        filename = record["filename"]
        require(record.get("digests", {}).get("sha256") == actual[filename], "PYPI_WHEEL_DIGEST_MISMATCH")
        matched.add(filename)
    require(matched == set(actual), "PYPI_UNEXPECTED_WHEEL")
    return actual


def rewrite_entrypoints(stage, site_paths):
    """Relocate generated text launchers and their wheel RECORD checksums."""
    old = str(stage / "venv").encode()
    new = str(ROOT / "venv").encode()
    changed = {}
    for path in (stage / "venv/bin").iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if old not in data:
            continue
        require(data.startswith(b"#!") and b"\x00" not in data, "UNKNOWN_STAGED_ENTRYPOINT")
        data = data.replace(old, new)
        path.write_bytes(data)
        changed[path.resolve()] = data
    updated = set()
    for site_path in site_paths:
        for record in Path(site_path).glob("*.dist-info/RECORD"):
            rows = list(csv.reader(io.StringIO(record.read_text(encoding="utf-8"))))
            dirty = False
            for row in rows:
                if len(row) != 3:
                    continue
                path = (Path(site_path) / row[0]).resolve()
                if path in changed:
                    data = changed[path]
                    row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                    row[2] = str(len(data))
                    dirty = True
                    updated.add(path)
            if dirty:
                with record.open("w", encoding="utf-8", newline="") as target:
                    csv.writer(target).writerows(rows)
    require(updated == set(changed), "ENTRYPOINT_RECORD_MISSING")
    for path in (stage / "venv/bin").iterdir():
        if path.is_symlink():
            require(str(stage) not in os.readlink(path), "STAGED_BINARY_LINK_REMAINS")
        elif path.is_file():
            require(old not in path.read_bytes(), "STAGED_ENTRYPOINT_REMAINS")
    return sorted(str(path.relative_to(stage / "venv")) for path in changed)


def check_dependency_records(venv, site_paths):
    """Pip may uninstall only files inside its copied environment."""
    for site_path in site_paths:
        for record in Path(site_path).glob("*.dist-info/RECORD"):
            name = record.parent.name.lower().replace("_", "-")
            if not any(name.startswith(package + "-") for package in DEPENDENCIES):
                continue
            regular(record)
            require(record.resolve().is_relative_to(venv), "DEPENDENCY_RECORD_OUTSIDE_STAGE")
            for row in csv.reader(io.StringIO(record.read_text(encoding="utf-8"))):
                require(len(row) == 3 and row[0], "DEPENDENCY_RECORD_INVALID")
                path = (Path(site_path) / row[0]).resolve()
                require(path.is_relative_to(venv), "DEPENDENCY_RECORD_OUTSIDE_STAGE")


def prepare_dependencies(stage):
    live = ROOT / "venv"
    canonical_directory(live)
    signature = venv_signature(live)
    baseline = probe_venv(live, stage)
    require(not ({"opencv-python", "opencv-contrib-python", "opencv-contrib-python-headless"}
                 & set(baseline["packages"])), "CONFLICTING_OPENCV_DISTRIBUTION")
    target = stage / "venv"
    require(not target.exists(), "STAGED_VENV_ALREADY_EXISTS")
    shutil.copytree(live, target, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    require(venv_signature(live) == signature, "LIVE_DEPENDENCIES_CHANGED_DURING_COPY")
    staged = probe_venv(target, stage)
    require(staged["packages"] == baseline["packages"], "VENV_COPY_INVENTORY_CHANGED")
    check_dependency_records(target, staged["site_packages"])
    wheels = stage / "wheels"
    wheels.mkdir(mode=0o700)
    python = str(target / "bin/python")
    pip = [python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check"]
    packages = [name + "==" + version for name, version in DEPENDENCIES.items()]
    print("DEPENDENCY_STAGING=START; LIVE_VENV_WRITES=NONE", flush=True)
    isolated_run([*pip, "download", "--no-cache-dir", "--only-binary=:all:", "--no-deps",
                  "--index-url", "https://pypi.org/simple", "--timeout", "30", "--retries", "2",
                  "--dest", str(wheels), *packages], stage, "DEPENDENCY_DOWNLOAD_FAILED", 300)
    wheel_paths = sorted(wheels.glob("*.whl"))
    require(len(wheel_paths) == len(DEPENDENCIES), "UNEXPECTED_WHEEL_COUNT")
    wheel_hashes = verified_wheel_hashes(wheel_paths)
    isolated_run([*pip, "install", "--no-index", "--no-cache-dir", "--no-deps", "--no-compile",
                  "--no-user", "--upgrade", "--prefix", str(target),
                  *map(str, wheel_paths)], stage, "STAGED_DEPENDENCY_INSTALL_FAILED", 180)
    staged = probe_venv(target, stage, dependencies=True)
    expected = {**baseline["packages"], **DEPENDENCIES}
    require(staged["packages"] == expected, "UNAUTHORIZED_DEPENDENCY_CHANGE")
    isolated_run([*pip, "check"], stage, "DEPENDENCY_CONFLICT", 60)
    scripts = rewrite_entrypoints(stage, staged["site_packages"])
    # Expose only traversable staging to the known, nonroot service account;
    # all other private staging entries remain mode0700/0600.
    user = service_user()
    os.chown(stage, 0, user.pw_gid)
    os.chmod(stage, 0o750)
    probe_venv(target, stage, dependencies=True, user=user)
    require(venv_signature(live) == signature, "LIVE_DEPENDENCIES_CHANGED_DURING_STAGING")
    report = {"before": baseline["packages"], "after": staged["packages"],
              "live_venv_signature": signature, "staged_venv_signature": venv_signature(target),
              "python": staged["python"], "wheel_sha256": wheel_hashes,
              "rewritten_entrypoints": scripts, "service_uid": user.pw_uid,
              "service_gid": user.pw_gid, "live_writes": False}
    (stage / "dependency-manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("DEPENDENCY_STAGING=PASS; NUMPY=2.2.6; OPENCV_HEADLESS=4.12.0.88; LIVE_VENV_WRITES=NONE", flush=True)
    return report


def validate_dependencies(stage):
    regular(stage / "dependency-manifest.json")
    report = json.loads((stage / "dependency-manifest.json").read_text(encoding="utf-8"))
    require(venv_signature(ROOT / "venv") == report["live_venv_signature"], "LIVE_DEPENDENCIES_CHANGED")
    require(venv_signature(stage / "venv") == report["staged_venv_signature"], "STAGED_DEPENDENCIES_CHANGED")
    require(report["after"] == {**report["before"], **DEPENDENCIES}, "DEPENDENCY_MANIFEST_CHANGED")
    for name, expected in report["wheel_sha256"].items():
        require(Path(name).name == name and digest(stage / "wheels" / name) == expected, "WHEEL_HASH_CHANGED")
    return report


def swap_venv(stage, backup_path, report):
    live = ROOT / "venv"
    require(not backup_path.exists() and not backup_path.is_symlink(), "VENV_BACKUP_EXISTS")
    require(live.stat().st_dev == (stage / "venv").stat().st_dev == backup_path.parent.stat().st_dev,
            "VENV_SWAP_MUST_STAY_ON_FILESYSTEM")
    require(venv_signature(live) == report["live_venv_signature"], "LIVE_DEPENDENCIES_CHANGED")
    require(venv_signature(stage / "venv") == report["staged_venv_signature"], "STAGED_DEPENDENCIES_CHANGED")
    os.replace(live, backup_path)
    sync_directory(ROOT)
    os.replace(stage / "venv", live)
    sync_directory(ROOT)
    require(venv_signature(live) == report["staged_venv_signature"], "INSTALLED_DEPENDENCIES_CHANGED")


def restore_venv(stage, backup_path, report):
    if not backup_path.exists():
        require(venv_signature(ROOT / "venv") == report["live_venv_signature"], "VENV_BACKUP_MISSING")
        return
    require(venv_signature(backup_path) == report["live_venv_signature"], "VENV_BACKUP_CHANGED")
    live = ROOT / "venv"
    if live.exists() or live.is_symlink():
        require(venv_signature(live) == report["staged_venv_signature"], "ROLLBACK_DEPENDENCY_THIRD_PARTY_CHANGE")
        failed = stage / "failed-venv"
        require(not failed.exists(), "FAILED_VENV_ALREADY_EXISTS")
        os.replace(live, failed)
    os.replace(backup_path, live)
    sync_directory(ROOT)
    require(venv_signature(live) == report["live_venv_signature"], "DEPENDENCY_ROLLBACK_VERIFY_FAILED")


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45, check=False)
    require(result.returncode == 0, "COMMAND_FAILED: " + args[0])
    return result.stdout.strip()


def active():
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False).returncode == 0


def stop():
    command("systemctl", "stop", SERVICE)
    require(not active(), "SERVICE_NOT_STOPPED")


def http_get(path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, url):
            return None
    # No proxy, authentication, cookie jar, redirects or mutating requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(LOCAL + path, timeout=3) as response:
            return response.status, response.read(128), response.headers.get("Location", "")
    except urllib.error.HTTPError as error:
        return error.code, b"", error.headers.get("Location", "")


def smoke():
    require(active(), "SERVICE_NOT_ACTIVE")
    status, body, _ = http_get("/health")
    require(status == 200 and body.strip() == b"ok", "SERVICE_HEALTH_FAILED")
    base = "/accounting/medical-supplies/incoming/"
    for route in (base, base + "1/ocr"):
        status, _, location = http_get(route)
        require(status == 302 and location.startswith("/login"), "OCR_AUTH_GET_FAILED")
    for route in (base + "1/ocr/page", base + "1/ocr/save"):
        require(http_get(route)[0] == 405, "OCR_POST_ONLY_ROUTE_FAILED")
    print("SERVICE_HEALTH=PASS; MEDICAL_GET_ROUTES=PASS", flush=True)


def start():
    command("systemctl", "start", SERVICE)
    for _ in range(20):
        try:
            status, body, _ = http_get("/health")
            if active() and status == 200 and body.strip() == b"ok":
                smoke()
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("RESTART_HEALTH_FAILED")


def ro_conn(path):
    regular(path)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def check_db(path):
    with closing(ro_conn(path)) as conn:
        require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "DATABASE_CHECK_FAILED")
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require({"med_supply_documents", "med_supply_document_events", "med_supply_items",
                 "med_supply_folders", "med_supply_daily_items"} <= names, "MEDICAL_SCHEMA_MISSING")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(med_supply_documents)")}
        require({"original_bytes", "sha256", "fields_json", "version"} <= columns, "DOCUMENT_COLUMNS_MISSING")


def database_bytes():
    # Main DB and WAL hold data. SHM lock metadata is deliberately excluded.
    # Compare only while the service is stopped, never after normal traffic resumes.
    result = {DB.name: digest(DB)}
    wal = Path(str(DB) + "-wal")
    if wal.exists() or wal.is_symlink():
        result[wal.name] = digest(wal)
    else:
        result[wal.name] = None
    return result


def backup_database(path):
    with closing(ro_conn(DB)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
        target.commit()
    os.chmod(path, 0o600)
    check_db(path)
    return digest(path)


def protected_files():
    result = {}
    for name in PROTECTED:
        path = ROOT / name
        result[name] = digest(path) if path.exists() or path.is_symlink() else None
    return result


def check_live():
    for directory in (ROOT, ROOT / "templates", DB.parent, BACKUPS):
        canonical_directory(directory)
    for name, (old, _) in FILES.items():
        require(file_state(ROOT / name) == old, "LIVE_BASE_MISMATCH: " + name)


def validate_stage(stage):
    import jinja2
    for name, (_, target) in FILES.items():
        path = stage / name
        check_blob(path, target, "STAGED_TARGET_MISMATCH")
        if name.endswith(".py"):
            compile(path.read_bytes(), name, "exec")
        elif name.endswith(".html"):
            jinja2.Environment().parse(path.read_text(encoding="utf-8"))


def prepare(stage):
    for name, (_, expected) in FILES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{MERGED}/az-baze-auth/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "AZ-BAZE-PR273-Release"})
        data = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    require(response.status == 200, "DOWNLOAD_STATUS_FAILED")
                    data = response.read(512 * 1024 + 1)
                break
            except (OSError, urllib.error.URLError):
                if attempt:
                    raise RuntimeError("SOURCE_DOWNLOAD_FAILED") from None
                time.sleep(1)
        require(data is not None and len(data) <= 512 * 1024 and blob(data) == expected,
                "EXACT_SOURCE_MISMATCH")
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    validate_stage(stage)
    print("EXACT_TARGET=PASS; PYTHON_JINJA=PASS; FILES=6", flush=True)


def atomic_replace(data, path, metadata, expected_blobs=None):
    require(path.parent.resolve() == path.parent, "TARGET_PARENT_NOT_CANONICAL")
    current = file_state(path)
    if expected_blobs is not None:
        require(current in expected_blobs, "REPLACE_THIRD_PARTY_CHANGE")
    fd, filename = tempfile.mkstemp(prefix=".pr273-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fchown(target.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(target.fileno(), metadata["mode"])
            os.fsync(target.fileno())
        if expected_blobs is not None:
            require(file_state(path) in expected_blobs, "REPLACE_THIRD_PARTY_CHANGE")
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def restore(backup, metadata):
    # Validate the entire rollback set before replacing a single file.
    for name, (old, target) in FILES.items():
        if old is not None:
            check_blob(backup / "files" / name, old, "BACKUP_HASH_FAILED")
        require(file_state(ROOT / name) in (old, target), "ROLLBACK_THIRD_PARTY_CHANGE")
    for name in reversed(tuple(FILES)):
        old, _ = FILES[name]
        if old is None:
            current = file_state(ROOT / name)
            require(current in FILES[name], "ROLLBACK_THIRD_PARTY_CHANGE")
            if current is not None:
                (ROOT / name).unlink()
                sync_directory((ROOT / name).parent)
        else:
            atomic_replace((backup / "files" / name).read_bytes(), ROOT / name, metadata[name], FILES[name])
        require(file_state(ROOT / name) == old, "ROLLBACK_VERIFY_FAILED")


def sync_backup(backup):
    for path in [*(backup / "files" / name for name, (old, _) in FILES.items() if old is not None),
                 backup / "auth-pre-pr273.sqlite", backup / "release-manifest.json"]:
        regular(path)
        with path.open("rb") as saved:
            os.fsync(saved.fileno())
    for path in (backup / "files" / "templates", backup / "files", backup, BACKUPS):
        sync_directory(path)


def apply(stage):
    validate_stage(stage)
    check_live()
    dependencies = validate_dependencies(stage)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUPS / ("pr273-medical-auto-" + stamp)
    venv_backup = ROOT / (".pr273-venv-backup-" + stamp)
    backup.mkdir(mode=0o700, exist_ok=False)
    stop_attempted = touched = dependency_attempted = False
    metadata = {}
    try:
        stop_attempted = True
        stop()
        check_live()
        check_db(DB)
        before = database_bytes()
        protected = protected_files()
        default_info = (ROOT / "medical_supplies_ocr.py").stat()
        for name, (old, _) in FILES.items():
            path = ROOT / name
            info = path.stat() if old is not None else default_info
            metadata[name] = {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}
            if old is not None:
                saved = backup / "files" / name
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(path, saved)
                check_blob(saved, old, "BACKUP_HASH_FAILED")
        db_digest = backup_database(backup / "auth-pre-pr273.sqlite")
        require(database_bytes() == before, "DATABASE_CHANGED_DURING_BACKUP")
        manifest = {"release": "PR273", "base": BASE, "audited_head": AUDITED,
                    "merged_sha": MERGED, "tree": TREE, "files": FILES, "metadata": metadata,
                    "dependencies": dependencies, "dependency_backup": str(venv_backup),
                    "database_backup_sha256": db_digest, "database_before": before,
                    "protected_files": protected, "auto_db_restore": False,
                    "created_utc": datetime.now(timezone.utc).isoformat()}
        (backup / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        sync_backup(backup)
        print("BACKUP=PASS " + str(backup), flush=True)
        validate_stage(stage)
        check_live()
        validate_dependencies(stage)
        touched = True
        for name, (old, target) in FILES.items():
            require(file_state(ROOT / name) == old, "INSTALL_BASE_MISMATCH")
            atomic_replace((stage / name).read_bytes(), ROOT / name, metadata[name], (old,))
            check_blob(ROOT / name, target, "INSTALL_TARGET_MISMATCH")
        dependency_attempted = True
        swap_venv(stage, venv_backup, dependencies)
        user = service_user()
        probe_venv(ROOT / "venv", stage, dependencies=True, user=user)
        require(database_bytes() == before, "DATABASE_CHANGED_BEFORE_START")
        require(protected_files() == protected, "PROTECTED_RUNTIME_CHANGED")
        print("DB_UNCHANGED_BEFORE_START=PASS; DATABASE_WRITES_BY_INSTALLER=NONE", flush=True)
        start()
        for name, (_, target) in FILES.items():
            check_blob(ROOT / name, target, "POST_RELEASE_HASH_MISMATCH")
        require(protected_files() == protected, "PROTECTED_RUNTIME_CHANGED")
        require(venv_signature(ROOT / "venv") == dependencies["staged_venv_signature"],
                "POST_RELEASE_DEPENDENCIES_CHANGED")
        print("PR273_RELEASE=PASS; RUNTIME_FILES=5; REQUIREMENTS_FILES=1; DEPENDENCIES=2", flush=True)
        print("DEPENDENCY_BACKUP=" + str(venv_backup), flush=True)
        print("MERGED_SHA=" + MERGED, flush=True)
        print("REAL_DOCUMENT_ACCEPTANCE=PENDING; PR270=DEFERRED", flush=True)
        return backup
    except BaseException:
        print("PR273_RELEASE=FAIL; BACKUP=" + str(backup), flush=True)
        if stop_attempted:
            try:
                if touched:
                    stop()
                    restore(backup, metadata)
                if dependency_attempted:
                    restore_venv(stage, venv_backup, dependencies)
                start()
                print("CODE_ROLLBACK=PASS; DEPENDENCY_ROLLBACK=PASS; DATABASE_NOT_RESTORED", flush=True)
            except BaseException:
                print("CODE_OR_DEPENDENCY_ROLLBACK=FAILED; DATABASE_NOT_RESTORED; BACKUP=" + str(backup), flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "vdska", "RUN_IN_ROOT_VDSKA")
    os.umask(0o077)
    fd = None
    try:
        if args.apply:
            fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                    "UNSAFE_RELEASE_LOCK")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_live()
        smoke()
        check_db(DB)
        service_user()
        wal = Path(str(DB) + "-wal")
        needed = DB.stat().st_size + (wal.stat().st_size if wal.is_file() else 0) + 64 * 1024 * 1024
        require(shutil.disk_usage(BACKUPS).free >= needed, "BACKUP_SPACE_LOW")
        venv_bytes = sum(path.stat().st_size for path in (ROOT / "venv").rglob("*")
                         if path.is_file() and not path.is_symlink())
        require(shutil.disk_usage(ROOT).free >= venv_bytes * 2 + 512 * 1024 * 1024,
                "VENV_STAGING_SPACE_LOW")
        with tempfile.TemporaryDirectory(prefix=".pr273-stage-", dir=ROOT) as directory:
            stage = Path(directory)
            prepare(stage)
            prepare_dependencies(stage)
            validate_dependencies(stage)
            print("PR273_PREFLIGHT=PASS; LIVE_BASE=PASS; RUNTIME_FILES=5; REQUIREMENTS_FILES=1; EXPECTED_BASE=" + BASE,
                  flush=True)
            if args.apply:
                apply(stage)
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        # Fixed operational markers only; never print HTTP bodies, SQL rows or credentials.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("PR273_STOP=" + message[:250], file=sys.stderr, flush=True)
        sys.exit(1)
