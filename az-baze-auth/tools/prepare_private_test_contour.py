#!/usr/bin/env python3
import argparse
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

EXPECTED_HOSTNAME = "az-server"
LIVE_SERVICE = "az-baze-auth"
LIVE_DB = Path("/var/lib/az-baze/auth.db")

TEST_SERVICE = "az-baze-test"
TEST_PORT = 8002
TEST_OPT_ROOT = Path("/opt/az-baze-test")
TEST_REPO = TEST_OPT_ROOT / "repo"
TEST_VENV = TEST_OPT_ROOT / "venv"
TEST_STATE_ROOT = Path("/var/lib/az-baze-test")
TEST_DB = TEST_STATE_ROOT / "auth.db"
TEST_SITE_ROOT = Path("/var/www/az-baze-test")
TEST_ENV_DIR = Path("/etc/az-baze-test")
TEST_ENV_FILE = TEST_ENV_DIR / "auth.env"
TEST_UNIT = Path("/etc/systemd/system/az-baze-test.service")
TEST_ACCESS_FILE = Path("/root/az-baze-test-access.txt")

REPOSITORY_URL = (
    "https://github.com/pat16071975-ship-it/architecture-health-site.git"
)
CONFIRMATION = "CREATE-PRIVATE-AZ-TEST"
TEST_EMAIL = "owner-private-test@local.invalid"
TEST_CLINIC_TIMEZONE = "UTC"

SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PrivateTestError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise PrivateTestError(message)


def run(command, *, cwd=None, env=None, check_result=True):
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check_result and process.returncode != 0:
        raise PrivateTestError(
            f"command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout={process.stdout}\nstderr={process.stderr}"
        )
    return process


def service_active(name):
    return (
        run(
            ["systemctl", "is-active", "--quiet", name],
            check_result=False,
        ).returncode
        == 0
    )


def validate_source_commit(value):
    value = str(value or "").strip()
    check(bool(SOURCE_COMMIT_RE.fullmatch(value)), "source commit must be 40 lowercase hex chars")
    return value


def port_is_free():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", TEST_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def test_paths():
    return (
        TEST_OPT_ROOT,
        TEST_STATE_ROOT,
        TEST_SITE_ROOT,
        TEST_ENV_DIR,
        TEST_UNIT,
        TEST_ACCESS_FILE,
    )


def preflight(source_commit, *, require_clean=False):
    source_commit = validate_source_commit(source_commit)
    check(socket.gethostname() == EXPECTED_HOSTNAME, f"hostname must be {EXPECTED_HOSTNAME}")
    check(LIVE_DB.exists() and LIVE_DB.is_file(), f"live DB missing: {LIVE_DB}")
    check(service_active(LIVE_SERVICE), f"live service is not active: {LIVE_SERVICE}")
    check(not service_active(TEST_SERVICE), f"test service is already active: {TEST_SERVICE}")
    check(port_is_free(), f"test loopback port is already in use: {TEST_PORT}")

    pwd.getpwnam("www-data")
    check(shutil.which("git"), "git is not installed")
    check(shutil.which("python3"), "python3 is not installed")
    check(shutil.which("systemctl"), "systemctl is not installed")

    existing = [str(path) for path in test_paths() if path.exists()]
    if require_clean:
        check(not existing, f"private test paths already exist: {existing}")

    return {
        "hostname": socket.gethostname(),
        "live_service": "active",
        "live_db": str(LIVE_DB),
        "source_commit": source_commit,
        "test_service": TEST_SERVICE,
        "test_bind": f"127.0.0.1:{TEST_PORT}",
        "test_paths_existing": existing,
        "live_db_write": False,
        "live_deploy": False,
        "nginx_change": False,
        "firewall_change": False,
    }


def render_test_home():
    return """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AZ-BAZE — приватный test-контур</title>
<style>
:root{--paper:#f7f3ec;--paper2:#efe8db;--ink:#2d342f;--green:#44634f;--gold:#b59662}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:linear-gradient(180deg,#fcfaf6,var(--paper) 65%,var(--paper2));color:var(--ink);font-family:Arial,sans-serif}
.shell{width:min(760px,92vw);margin:0 auto;padding:70px 0;text-align:center}
h1{font-weight:500}.muted{color:#6c746d;font-size:13px;line-height:1.6}
.menu{display:grid;gap:10px;width:min(420px,100%);margin:30px auto}
.menu-btn{display:block;padding:13px 16px;border:1px solid rgba(68,99,79,.34);border-radius:10px;background:#fff;color:#334039;text-decoration:none;font-weight:700}
.placeholder{display:none}
</style>
</head>
<body>
<div class="shell">
  <div style="font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:#607067;font-weight:700">Медицинская клиника системного подхода</div>
  <h1>Архитектура здоровья</h1>
  <p class="muted">Приватный test-контур. Здесь можно безопасно проверять настройки, не затрагивая рабочую систему.</p>
  <div class="menu">
    <a class="menu-btn active" href="/reports/">Отчёты</a>
    <a class="menu-btn active" href="/knowledge/" id="knowledge">База знаний</a>
    <div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>
    <div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>
    <div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>
  </div>
</div>
</body>
</html>
"""


def render_env(secret_key):
    return "\n".join(
        [
            "AZBAZE_TEST_CONTOUR=1",
            f"AZBAZE_TEST_STATE_ROOT={TEST_STATE_ROOT}",
            f"AZBAZE_DB={TEST_DB}",
            f"AZBAZE_SITE_ROOT={TEST_SITE_ROOT}",
            f"AZBAZE_SECRET_KEY={secret_key}",
            f"AZBAZE_CREDENTIALS_KEY={TEST_STATE_ROOT / 'credentials.key'}",
            f"AZ_FINREZ_DATA_PATH={TEST_STATE_ROOT / 'finrez-data.enc'}",
            f"AZ_FINREZ_KEY_PATH={TEST_STATE_ROOT / 'finrez.key'}",
            "AZBAZE_CREDENTIALS_PURGE_EMAILS=",
            "",
        ]
    )


def render_unit():
    return f"""[Unit]
Description=AZ-BAZE private test contour
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory={TEST_REPO / 'az-baze-auth'}
EnvironmentFile={TEST_ENV_FILE}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={TEST_VENV / 'bin/gunicorn'} --workers 1 --threads 2 --bind 127.0.0.1:{TEST_PORT} --access-logfile - --error-logfile - test_server:app
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
IPAddressDeny=any
IPAddressAllow=localhost
ReadOnlyPaths={TEST_REPO} {TEST_SITE_ROOT}
ReadWritePaths={TEST_STATE_ROOT}

[Install]
WantedBy=multi-user.target
"""


def environment_from_file():
    env = dict(os.environ)
    for raw in TEST_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def render_bootstrap_database_script(password):
    return f'''
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, {str(TEST_REPO / 'az-baze-auth')!r})

import app
import db_migrations
import structure_seed
from werkzeug.security import generate_password_hash

db_path = Path(os.environ["AZBAZE_DB"])
app.init_db_file(db_path)

migrations_dir = Path({str(TEST_REPO / 'az-baze-auth' / 'migrations')!r})
result = db_migrations.apply_migrations(db_path, migrations_dir)
if result != {{"applied": ["20260920_001"], "already_applied": []}}:
    raise RuntimeError(f"unexpected test migration result: {{result}}")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys=ON")
try:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM permissions")
    conn.execute("DELETE FROM users")
    conn.execute(
        """
        INSERT INTO users(
            email,full_name,password_hash,is_admin,active,must_change_password,
            failed_attempts,locked_until,created_at,updated_at
        ) VALUES(?,?,?,0,1,0,0,NULL,?,?)
        """,
        (
            {TEST_EMAIL!r},
            "Владелец — приватный test-контур",
            generate_password_hash({password!r}),
            now,
            now,
        ),
    )
    user_id = conn.execute("SELECT id FROM users WHERE email=?", ({TEST_EMAIL!r},)).fetchone()[0]
    conn.execute(
        "INSERT INTO permissions(user_id,section) VALUES(?,?)",
        (user_id, "structure_manage"),
    )

    structure_seed.seed_initial_structure(
        conn,
        holding_name="Архитектура здоровья — TEST",
        organization_name="Архитектура здоровья — TEST",
        cluster_name="Кластер по умолчанию — TEST",
        clinic_name="Архитектура здоровья — TEST",
        timezone_name={TEST_CLINIC_TIMEZONE!r},
    )
    conn.commit()

    users = conn.execute("SELECT email,is_admin,active FROM users ORDER BY id").fetchall()
    if len(users) != 1 or users[0][0] != {TEST_EMAIL!r} or users[0][1] != 0 or users[0][2] != 1:
        raise RuntimeError(f"unexpected private test users: {{users}}")
    perms = conn.execute("SELECT section FROM permissions WHERE user_id=?", (user_id,)).fetchall()
    if [row[0] for row in perms] != ["structure_manage"]:
        raise RuntimeError(f"unexpected private test permissions: {{perms}}")
    clinic = [
        tuple(row)
        for row in conn.execute(
            "SELECT name,timezone FROM clinics"
        ).fetchall()
    ]
    if clinic != [("Архитектура здоровья — TEST", {TEST_CLINIC_TIMEZONE!r})]:
        raise RuntimeError(f"unexpected private test clinic: {{clinic}}")
finally:
    conn.close()
'''


def initialize_test_database(password):
    bootstrap = render_bootstrap_database_script(password)
    env = environment_from_file()
    run([str(TEST_VENV / "bin/python"), "-c", bootstrap], env=env)


def health_ok():
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{TEST_PORT}/health",
            timeout=3,
        ) as response:
            return response.status == 200 and response.read().decode("utf-8").strip() == "ok"
    except Exception:
        return False


def cleanup_failed_prepare():
    run(["systemctl", "stop", TEST_SERVICE], check_result=False)
    if TEST_UNIT.exists():
        TEST_UNIT.unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"], check_result=False)

    for path in (TEST_OPT_ROOT, TEST_STATE_ROOT, TEST_SITE_ROOT, TEST_ENV_DIR):
        if path.exists():
            shutil.rmtree(path)

    TEST_ACCESS_FILE.unlink(missing_ok=True)


def prepare(source_commit):
    check(os.geteuid() == 0, "prepare must run as root")
    info = preflight(source_commit, require_clean=True)

    password = secrets.token_urlsafe(18)
    secret_key = secrets.token_urlsafe(48)

    try:
        TEST_OPT_ROOT.mkdir(parents=True, mode=0o755)
        TEST_STATE_ROOT.mkdir(parents=True, mode=0o700)
        TEST_SITE_ROOT.mkdir(parents=True, mode=0o755)
        TEST_ENV_DIR.mkdir(parents=True, mode=0o700)

        run(["git", "clone", "--no-checkout", REPOSITORY_URL, str(TEST_REPO)])
        run(["git", "-C", str(TEST_REPO), "checkout", "--detach", source_commit])
        actual = run(
            ["git", "-C", str(TEST_REPO), "rev-parse", "HEAD"]
        ).stdout.strip()
        check(actual == source_commit, f"checked out wrong source: {actual}")

        run(["python3", "-m", "venv", str(TEST_VENV)])
        run(
            [
                str(TEST_VENV / "bin/pip"),
                "install",
                "--disable-pip-version-check",
                "-r",
                str(TEST_REPO / "az-baze-auth" / "requirements.txt"),
            ]
        )

        TEST_ENV_FILE.write_text(render_env(secret_key), encoding="utf-8")
        os.chmod(TEST_ENV_FILE, 0o600)

        (TEST_SITE_ROOT / "index.html").write_text(
            render_test_home(),
            encoding="utf-8",
        )

        initialize_test_database(password)

        www = pwd.getpwnam("www-data")
        for path in TEST_STATE_ROOT.rglob("*"):
            if path.is_file():
                os.chown(path, www.pw_uid, www.pw_gid)
                os.chmod(path, 0o600)
            elif path.is_dir():
                os.chown(path, www.pw_uid, www.pw_gid)
                os.chmod(path, 0o700)
        os.chown(TEST_STATE_ROOT, www.pw_uid, www.pw_gid)
        os.chmod(TEST_STATE_ROOT, 0o700)

        TEST_UNIT.write_text(render_unit(), encoding="utf-8")
        os.chmod(TEST_UNIT, 0o644)

        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", TEST_SERVICE])

        for _ in range(30):
            if health_ok():
                break
            time.sleep(0.5)
        check(health_ok(), "private test service failed health check")

        access = "\n".join(
            [
                "AZ-BAZE PRIVATE TEST CONTOUR",
                f"SOURCE_COMMIT={source_commit}",
                "BIND=127.0.0.1:8002",
                f"LOGIN_EMAIL={TEST_EMAIL}",
                f"LOGIN_PASSWORD={password}",
                f"TEST_TIMEZONE_INITIAL={TEST_CLINIC_TIMEZONE}",
                "",
                "LOCAL TUNNEL (run on your Windows PC):",
                "ssh -N -L 18002:127.0.0.1:8002 az-server",
                "",
                "OPEN IN BROWSER:",
                "http://127.0.0.1:18002",
                "",
                "IMPORTANT:",
                "- test service is not exposed by nginx",
                "- test service listens only on server loopback",
                "- test DB is newly created and contains no live users/data",
                "- only the dedicated test account above is enabled",
                "- live DB/service/site were not modified",
                "",
            ]
        )
        TEST_ACCESS_FILE.write_text(access, encoding="utf-8")
        os.chmod(TEST_ACCESS_FILE, 0o600)

        return {
            **info,
            "prepared": True,
            "source_commit": source_commit,
            "test_login_email": TEST_EMAIL,
            "test_login_password": password,
            "test_initial_timezone": TEST_CLINIC_TIMEZONE,
            "test_service": TEST_SERVICE,
            "test_bind": f"127.0.0.1:{TEST_PORT}",
            "browser_url_via_tunnel": "http://127.0.0.1:18002",
            "access_file": str(TEST_ACCESS_FILE),
        }
    except Exception:
        cleanup_failed_prepare()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare isolated private AZ-BAZE test contour"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    if args.preflight:
        info = preflight(args.source_commit, require_clean=False)
        print("PRIVATE TEST PREFLIGHT: PASS")
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    check(args.confirm == CONFIRMATION, f"confirmation mismatch; required: {CONFIRMATION}")
    result = prepare(args.source_commit)
    print("PRIVATE TEST CONTOUR: PASS")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    print(f"LOGIN_EMAIL={result['test_login_email']}")
    print(f"LOGIN_PASSWORD={result['test_login_password']}")
    print("LOCAL_TUNNEL=ssh -N -L 18002:127.0.0.1:8002 az-server")
    print("BROWSER=http://127.0.0.1:18002")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
