#!/usr/bin/env python3
"""AZ-BAZE PR76 private Test-only controlled apply. Paste via python3 - <<'PY'."""
import hashlib
import os
import pwd
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

REPO_URL = "https://github.com/pat16071975-ship-it/architecture-health-site.git"
BRANCH = "feature/az-baze-independent-three-uploads-p1"
TARGET = "1d5f3d7dd48631da79b681b447c77f2e782041fa"
ORIGINAL = "fee9fb3862765ffb96a598cc4f23b2d5c9383a94"
WRAPPER = "c98237b78a4b93f117fc1783d7223d2e64ffdf83"
TEST_OPT = Path("/opt/az-baze-test")
TEST_REPO = TEST_OPT / "repo"
TEST_VENV = TEST_OPT / "venv"
TEST_APP = TEST_REPO / "az-baze-auth"
TEST_SITE = Path("/var/www/az-baze-test")
TEST_REPORTS = TEST_SITE / "reports"
TEST_STATE = Path("/var/lib/az-baze-test")
TEST_DB = TEST_STATE / "auth.db"
TEST_UPLOADS = TEST_STATE / "nn-uploads"
TEST_ENV = Path("/etc/az-baze-test/auth.env")
TEST_UNIT = Path("/etc/systemd/system/az-baze-test.service")
TEST_EMAIL = "owner-private-test@local.invalid"
TEST_SERVICE = "az-baze-test"
LIVE_SERVICE = "az-baze-auth"
HEALTH_URL = "http://127.0.0.1:8002/health"
IDENT = uuid.uuid4().hex[:10]
STAGE = TEST_OPT / (".pr76-incoming-" + IDENT)
STAGED_REPO = STAGE / "repo"
STAGED_REPORTS = TEST_SITE / (".pr76-reports-" + IDENT)
BACKUP = TEST_OPT / (".pr76-rollback-" + IDENT)
DB_BACKUPS = TEST_STATE / "backups"
DB_BACKUP = DB_BACKUPS / ("pre-pr76-" + IDENT + ".sqlite")

class Blocked(Exception):
    pass

def require(condition, reason):
    if not condition:
        raise Blocked(reason)

def cmd(argv, *, cwd=None, env=None):
    result = subprocess.run(argv, cwd=cwd, env=env,
                            text=True, capture_output=True)
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        raise Blocked("command failed: " + " ".join(map(str, argv[:4])) +
                      "; " + details[-500:])
    return result.stdout.strip()

def service_status(name):
    p = subprocess.run(["systemctl", "is-active", name],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "inactive"

def git_head(repo):
    return cmd(["git", "-C", str(repo), "rev-parse", "HEAD"])

def git_blob(file):
    return cmd(["git", "hash-object", str(file)])

def under(path, root):
    resolved = path.resolve(strict=False)
    require(resolved == root or root in resolved.parents,
            "path escapes Test: " + str(path))

def test_health():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=7) as response:
            require(response.status == 200, "Test health HTTP != 200")
            require(response.read(80).decode().strip() == "ok", "Test health != ok")
            require(response.headers.get("X-AZ-BAZE-Contour") == "private-test",
                    "private-test response marker missing")
    except Exception as e:
        raise Blocked("Test health/marker failed: " + type(e).__name__) from e

def sqlite_connection(path, read_only=False):
    if read_only:
        return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    return sqlite3.connect(path)

def verify_sqlite(path):
    c = sqlite_connection(path, True)
    try:
        require(c.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                "Test DB quick_check failed")
        return c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        c.close()

def snapshot_hash(path):
    c = sqlite_connection(path, True)
    h = hashlib.sha256()
    try:
        for statement in c.iterdump():
            h.update(statement.encode("utf-8"))
            h.update(b"\n")
    finally:
        c.close()
    return h.hexdigest()

def test_users():
    c = sqlite_connection(TEST_DB, True)
    try:
        rows = c.execute("SELECT id,email,is_admin,active,must_change_password FROM users").fetchall()
        require(len(rows) == 1, "Test must have exactly one isolated user")
        uid, email, admin, active, password_change = rows[0]
        require(email == TEST_EMAIL and not admin and active == 1 and not password_change,
                "Test login identity/role does not match historical private user")
        return uid
    finally:
        c.close()

def env_lines():
    lines = TEST_ENV.read_text(encoding="utf-8").splitlines()
    vals = {}
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        require("=" in line, "invalid Test env line")
        key, value = line.split("=", 1)
        key = key.strip()
        require(key not in vals, "duplicate env key: " + key)
        vals[key] = value.strip().strip("\"'")
    expected = {
        "AZBAZE_TEST_CONTOUR":"1",
        "AZBAZE_TEST_STATE_ROOT":str(TEST_STATE),
        "AZBAZE_DB":str(TEST_DB),
        "AZBAZE_SITE_ROOT":str(TEST_SITE),
        "AZBAZE_CREDENTIALS_KEY":str(TEST_STATE / "credentials.key"),
        "AZ_FINREZ_DATA_PATH":str(TEST_STATE / "finrez-data.enc"),
        "AZ_FINREZ_KEY_PATH":str(TEST_STATE / "finrez.key"),
    }
    for key, value in expected.items():
        require(vals.get(key) == value, "unsafe Test env key: " + key)
    require(vals.get("AZBAZE_NN_UPLOAD_ROOT", str(TEST_UPLOADS)) == str(TEST_UPLOADS),
            "Test NN upload root points outside Test")
    require(vals.get("AZBAZE_SECRET_KEY"), "Test secret key missing")
    for key, value in vals.items():
        if key != "AZBAZE_SECRET_KEY":
            require(not any(x in value for x in (
                "/var/lib/az-baze/", "/var/www/az-baze.ru", "/opt/az-baze-auth")),
                "Production path in Test environment: " + key)
    return lines, vals

def test_process_env():
    _, test_values = env_lines()
    environment = dict(os.environ)
    environment.update(test_values)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_NO_CACHE_DIR"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    require(environment.get("AZBAZE_DB") == str(TEST_DB),
            "Test process DB path misconfigured")
    return environment

def system_guard():
    unit = TEST_UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=www-data", "Group=www-data",
        "WorkingDirectory=/opt/az-baze-test/repo/az-baze-auth",
        "EnvironmentFile=/etc/az-baze-test/auth.env",
        "--bind 127.0.0.1:8002", "test_server:app",
        "IPAddressDeny=any", "IPAddressAllow=localhost",
        "ProtectSystem=strict", "ReadWritePaths=/var/lib/az-baze-test",
    ):
        require(marker in unit, "Test unit isolation guard failed: " + marker)
    require("/opt/az-baze-auth" not in unit
            and "/var/lib/az-baze/" not in unit
            and "/var/www/az-baze.ru" not in unit,
            "production path in Test systemd unit")

def initial_guard():
    require(os.geteuid() == 0, "Run as root@az-server only")
    require(socket.gethostname().split(".")[0] == "az-server", "wrong server")
    for command in ("git", "systemctl", "python3"):
        require(shutil.which(command), "missing command " + command)
    require(service_status(LIVE_SERVICE) == "active", "Production service not active")
    require(service_status(TEST_SERVICE) == "active", "Test service not active")
    for path in (TEST_OPT, TEST_REPO, TEST_VENV, TEST_SITE, TEST_STATE,
                 TEST_DB, TEST_ENV, TEST_UNIT, TEST_APP / "test_server.py"):
        require(path.exists(), "Test path missing: " + str(path))
        require(not path.is_symlink(), "Test path is symlink: " + str(path))
    require(not TEST_REPORTS.exists() and not TEST_REPORTS.is_symlink(),
            "Test reports directory unexpectedly exists")
    require(not STAGE.exists() and not STAGED_REPORTS.exists() and not BACKUP.exists(),
            "staging/backup directory unexpectedly exists")
    require(not TEST_UPLOADS.is_symlink(), "unsafe symlink in Test NN root")
    if TEST_UPLOADS.exists():
        require(TEST_UPLOADS.is_dir(), "NN root is not a Test directory")
    live_db = Path("/var/lib/az-baze/auth.db")
    if live_db.exists():
        ts, ls = TEST_DB.stat(), live_db.stat()
        require((ts.st_dev,ts.st_ino)!=(ls.st_dev,ls.st_ino),
                "Test DB and Production DB are hardlinked")
    require(TEST_ENV.parent.resolve() == Path("/etc/az-baze-test"),
            "Test env parent path escapes isolated root")
    require(git_head(TEST_REPO) == ORIGINAL, "Test checkout drift since preflight")
    require(git_blob(TEST_APP / "test_server.py") == WRAPPER,
            "private Test wrapper does not match audited blob")
    system_guard()
    env_lines()
    verify_sqlite(TEST_DB)
    user_id = test_users()
    test_health()
    require(shutil.disk_usage(TEST_OPT).free > 1000 * 1024 * 1024,
            "Test disk free space is below 1 GB")
    print("PREFLIGHT=PASS | TEST=active | PRODUCTION=active | DB=ok | old_commit=" + ORIGINAL[:8])
    return user_id

def staging():
    STAGE.mkdir(mode=0o700)
    env = test_process_env()
    cmd(["git", "clone", "--depth", "1", "--single-branch", "--branch", BRANCH,
         REPO_URL, str(STAGED_REPO)], env=env)
    require(git_head(STAGED_REPO) == TARGET, "GitHub PR76 head is not exact expected SHA")
    audited = {
        "az-baze-auth/app.py":"40cab9120d48993b8c0b774289756f376e13b6ea",
        "az-baze-auth/server.py":"045816567ee8c2c4cad95bb2f6e686c5056ea745",
        "az-baze-auth/report_storage.py":"81d5371114417df19de349bd25792825d2f079df",
        "az-baze-auth/nn_upload.py":"70526f742ea53eebbcbeda1027ce0327f0817a21",
        "az-baze-auth/requirements.txt":"8b71edd4ad0e9cf54ac4673c5f2487cc8adc76ab",
        "reports/index.html":"d794ae64ce05a8e7e5f5dc7781f3ebc0bd136854",
        "reports/period-view.js":"addb67c56a7047c01a73509bfa46e41c010ccfa1",
    }
    for name, expected in audited.items():
        require(git_blob(STAGED_REPO / name) == expected,
                "target file blob mismatch: " + name)
    require(not (STAGED_REPO / "az-baze-auth/test_server.py").exists(),
            "unexpected new Test wrapper in PR76")
    shutil.copy2(TEST_APP / "test_server.py", STAGED_REPO / "az-baze-auth/test_server.py")
    require(git_blob(STAGED_REPO / "az-baze-auth/test_server.py") == WRAPPER,
            "Test wrapper not preserved")
    for f in (STAGED_REPO / "reports").rglob("*"):
        require(not f.is_symlink(), "symlink in PR76 report asset tree")
    shutil.copytree(STAGED_REPO / "reports", STAGED_REPORTS, symlinks=False)
    for name in ("index.html", "period-view.js", "dashboard.html", "forecast.html", "finrez.html"):
        require((STAGED_REPORTS / name).is_file(), "report asset missing: " + name)
    cmd([str(TEST_VENV / "bin/python"), "-m", "pip", "download", "--disable-pip-version-check",
         "--dest", str(STAGE / "wheels"), "-r",
         str(STAGED_REPO / "az-baze-auth/requirements.txt")], env=env)
    # Read-only migration catalogue check against *Test* SQLite before stopping it.
    cmd([str(TEST_VENV / "bin/python"), "-c",
         "import db_migrations,sys; db_migrations.migration_status(sys.argv[1],sys.argv[2])",
         str(TEST_DB), str(STAGED_REPO / "az-baze-auth/migrations")],
        cwd=STAGED_REPO / "az-baze-auth",
        env={**env,"PYTHONPATH":str(STAGED_REPO / "az-baze-auth")})
    require(shutil.disk_usage(TEST_OPT).free > 500 * 1024 * 1024,
            "low free disk after staging")
    print("TARGET=PASS | downloads=PASS | migrations_readonly=PASS | reports_staged=PASS")

def copy_backup(uid):
    BACKUP.mkdir(mode=0o700)
    shutil.copy2(TEST_ENV, BACKUP / "auth.env")
    shutil.copy2(TEST_UNIT, BACKUP / "az-baze-test.service")
    DB_BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    before = snapshot_hash(TEST_DB)
    source = sqlite_connection(TEST_DB, True)
    dest = sqlite3.connect(DB_BACKUP)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        source.close()
        dest.close()
    os.chmod(DB_BACKUP, 0o600)
    require(verify_sqlite(DB_BACKUP) >= 1, "Test backup failed SQLite integrity")
    require(snapshot_hash(DB_BACKUP) == before, "Test backup differs in logical SQL data")
    assert test_users() == uid
    print("TEST_BACKUP=PASS | Test SQLite/env/systemd saved; Production untouched")

def mark_nn_root():
    existed = TEST_UPLOADS.exists()
    if not existed:
        TEST_UPLOADS.mkdir(mode=0o700)
        account = pwd.getpwnam("www-data")
        os.chown(TEST_UPLOADS, account.pw_uid, account.pw_gid)
        os.chmod(TEST_UPLOADS, 0o700)
    return not existed

def update_test_env():
    lines, old_values = env_lines()
    if "AZBAZE_NN_UPLOAD_ROOT" not in old_values:
        lines.append("AZBAZE_NN_UPLOAD_ROOT=" + str(TEST_UPLOADS))
        TEST_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(TEST_ENV, 0o600)
    env_lines()

def smoke_test(uid):
    settings = test_process_env()
    assert settings["AZBAZE_DB"] == str(TEST_DB)
    code = """
from test_server import app
from app import DB_PATH
assert str(DB_PATH)=='/var/lib/az-baze-test/auth.db'
with app.test_client() as c:
    with c.session_transaction() as session:
        session['user_id']=int(__import__('sys').argv[1])
    r=c.get('/reports/management/')
    assert r.status_code==200,r.status_code
    assert r.headers.get('X-AZ-BAZE-Contour')=='private-test'
    assert 'period-view.js?v=20261011-1' in r.get_data(as_text=True)
    d=c.get('/api/reports/data')
    assert d.status_code==200,d.status_code
    assert isinstance(d.get_json().get('data'),dict)
print('SYNTHETIC_AUTH_REPORT_GET=PASS')
"""
    cmd([str(TEST_VENV / "bin/python"), "-c", code, str(uid)],
        cwd=TEST_APP, env=settings)

def restore_database():
    if not DB_BACKUP.exists():
        return
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(str(TEST_DB) + suffix)
        require(not path.is_symlink(), "unsafe Test SQLite journal symlink")
        if path.exists():
            path.unlink()
    shutil.copy2(DB_BACKUP, TEST_DB)
    os.chmod(TEST_DB, 0o600)
    www = pwd.getpwnam("www-data")
    os.chown(TEST_DB, www.pw_uid, www.pw_gid)

def rollback(created_nn, stopped, backup_verified):
    if not stopped:
        return "NOT_NEEDED"
    subprocess.run(["systemctl", "stop", TEST_SERVICE],capture_output=True)
    if (BACKUP / "venv").exists():
        if TEST_VENV.exists():
            shutil.rmtree(TEST_VENV)
        os.replace(BACKUP / "venv", TEST_VENV)
    if (BACKUP / "repo").exists():
        if TEST_REPO.exists():
            shutil.rmtree(TEST_REPO)
        os.replace(BACKUP / "repo", TEST_REPO)
    if TEST_REPORTS.exists():
        shutil.rmtree(TEST_REPORTS)
    if (BACKUP / "auth.env").exists():
        shutil.copy2(BACKUP / "auth.env", TEST_ENV)
    if backup_verified:
        restore_database()
    if created_nn and TEST_UPLOADS.exists():
        try:
            TEST_UPLOADS.rmdir()  # do not delete nonempty Test user data
        except OSError:
            pass
    cmd(["systemctl", "start", TEST_SERVICE])
    require(service_status(TEST_SERVICE)=="active", "old Test service failed restart")
    test_health()
    require(git_head(TEST_REPO)==ORIGINAL, "rollback Test git commit mismatch")
    return "PASS"

def main():
    uid = initial_guard()
    stopped = False
    created_nn = False
    backup_verified = False
    try:
        staging()
        cmd(["systemctl", "stop", TEST_SERVICE])
        stopped = True
        require(service_status(TEST_SERVICE)=="inactive", "Test service did not stop cleanly")
        copy_backup(uid)
        backup_verified = True
        # Preserve whole original Test code and venv by atomic Test-only renames.
        os.replace(TEST_REPO, BACKUP / "repo")
        os.replace(STAGED_REPO, TEST_REPO)
        require(git_head(TEST_REPO)==TARGET, "post-install Test source SHA mismatch")
        os.replace(TEST_VENV, BACKUP / "venv")
        cmd(["python3", "-m", "venv", str(TEST_VENV)])
        env=test_process_env()
        cmd([str(TEST_VENV / "bin/python"), "-m", "pip", "install", "--no-index",
             "--find-links", str(STAGE / "wheels"), "-r",
             str(TEST_APP / "requirements.txt")], env=env)
        os.replace(STAGED_REPORTS, TEST_REPORTS)
        created_nn = mark_nn_root()
        update_test_env()
        cmd([str(TEST_VENV / "bin/python"), str(TEST_APP / "db_migrations.py"),
             "apply", "--db", str(TEST_DB)], cwd=TEST_APP, env=test_process_env())
        c = sqlite_connection(TEST_DB)
        try:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT OR IGNORE INTO permissions(user_id,section) VALUES(?,?)",
                      (uid,"reports"))
            c.commit()
            found=c.execute("SELECT 1 FROM permissions WHERE user_id=? AND section='reports'",
                            (uid,)).fetchone()
            require(bool(found), "Test-only reports permission not granted")
        finally:
            c.close()
        require(verify_sqlite(TEST_DB)>0, "Test DB integrity changed unexpectedly")
        smoke_test(uid)
        cmd(["systemctl", "start", TEST_SERVICE])
        require(service_status(TEST_SERVICE)=="active", "Test service failed start")
        test_health()
        require(service_status(LIVE_SERVICE)=="active", "Production service not active after Test update")
        require(git_head(TEST_REPO)==TARGET, "post-health Test source SHA drift")
        require(TEST_REPORTS.joinpath("period-view.js").is_file(), "Test report asset missing")
        print("TEST_APPLY=PASS | TEST_RELEASE="+TARGET)
        print("TEST_HTTP=200 | TEST_REPORT_SMOKE=PASS | TEST_DB=ok | TEST_REPORTS_PERMISSION=PASS")
        print("BACKUP_TEST="+str(BACKUP))
        print("BACKUP_TEST_DB="+str(DB_BACKUP))
        print("PRODUCTION_RESTART=NONE | PRODUCTION_DB_WRITE=NONE | MERGE=NONE")
        print("PHYSICAL_UI_CHECK=NOT_YET_RUN")
    except BaseException as error:
        print("TEST_APPLY=FAIL | ERROR="+str(error)[:700],file=sys.stderr)
        try:
            print("TEST_ROLLBACK="+rollback(created_nn, stopped, backup_verified),file=sys.stderr)
        except BaseException as rb:
            print("TEST_ROLLBACK=FAIL | ERROR="+str(rb)[:500],file=sys.stderr)
            print("STOP: do not attempt further commands; preserve Test backups",file=sys.stderr)
        raise SystemExit(1)
    finally:
        if STAGE.exists():
            shutil.rmtree(STAGE)
        if STAGED_REPORTS.exists():
            shutil.rmtree(STAGED_REPORTS)

if __name__=="__main__":
    try:
        main()
    except BaseException as e:
        print("PREFLIGHT_OR_FATAL_ERROR="+str(e)[:650],file=sys.stderr)
        raise SystemExit(1)
