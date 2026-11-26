#!/usr/bin/env bash
# ONLY disposable GitHub-hosted CI. Never invoke this fixture on a clinic host.
set -Eeuo pipefail
test "$GITHUB_ACTIONS" = true
test "$RUNNER_OS" = Linux
test "$GITHUB_REF" = refs/heads/test/ident-006b-install-20261126
test "$(id -u)" -ne 0
test "$(ps -p 1 -o comm=)" = systemd
here="$(cd "$(dirname "$0")/../.." && pwd)"
lab=/root/ident006b-ci
test ! -e /var/lib/az-ident006b
test ! -e /opt/az-ident006b
test ! -e /etc/systemd/system/az-baze-auth.service
test ! -e /etc/nginx/sites-available/ident006b-fixture.conf
# Install prerequisite packages only on this ephemeral runner.
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx python3-venv
# Hosted runner images can give /opt to the runner user. The real installer
# must refuse such a parent. Normalize only the disposable CI parent directory,
# never the production host and never a recursive chmod/chown.
sudo stat -c 'LAB_PARENT=%n OWNER=%U:%G MODE=%a' / /opt /etc /var /var/lib /run /etc/systemd/system /etc/nginx /etc/nginx/conf.d
sudo chown root:root /opt
sudo chmod go-w /opt
sudo stat -c 'LAB_NORMALIZED=%n OWNER=%U:%G MODE=%a' /opt
sudo install -d -m 0700 "$lab"
sudo install -d -m 0755 "$lab/source" "$lab/wheels"
sudo cp -a "$here/." "$lab/source/"
sudo chown -R root:root "$lab/source"
sudo chmod -R go-w "$lab/source"
python3 -m venv "$RUNNER_TEMP/ident006b-build"
"$RUNNER_TEMP/ident006b-build/bin/python" -m pip --isolated --disable-pip-version-check download --index-url https://pypi.org/simple --require-hashes --only-binary=:all: --no-deps -r "$here/deploy/requirements.lock" -d "$RUNNER_TEMP"
sudo install -m 0644 "$RUNNER_TEMP/gunicorn-23.0.0-py3-none-any.whl" "$RUNNER_TEMP/packaging-26.4-py3-none-any.whl" "$lab/wheels/"
# Stand-in for the existing app, not its real code or data.
sudo tee "$lab/health.py" >/dev/null <<'PY'
from http.server import BaseHTTPRequestHandler,HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path=="/health" else 404)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self,*args): pass
HTTPServer(("127.0.0.1",8001),H).serve_forever()
PY
sudo tee /etc/systemd/system/az-baze-auth.service >/dev/null <<'UNIT'
[Unit]
Description=Synthetic existing AZ BAZE fixture
[Service]
ExecStart=/usr/bin/python3 /root/ident006b-ci/health.py
UNIT
sudo systemctl daemon-reload
sudo systemctl start az-baze-auth.service
sudo install -d -m 0700 /etc/nginx/ident006b-fixture
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=IDENT006B disposable test CA' \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout "$lab/ca.key" -out "$lab/ca.crt" >/dev/null 2>&1
sudo openssl req -newkey rsa:2048 -nodes -subj '/CN=ident-lab.test' \
  -keyout /etc/nginx/ident006b-fixture/server.key -out "$lab/leaf.csr" >/dev/null 2>&1
sudo tee "$lab/leaf.ext" >/dev/null <<'EXT'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:ident-lab.test,DNS:ident-alias.test
EXT
sudo openssl x509 -req -in "$lab/leaf.csr" -CA "$lab/ca.crt" -CAkey "$lab/ca.key" -CAcreateserial \
  -days 1 -extfile "$lab/leaf.ext" -out /etc/nginx/ident006b-fixture/server.crt >/dev/null 2>&1
sudo install -m 0644 "$lab/ca.crt" /usr/local/share/ca-certificates/ident006b-ci.crt
sudo update-ca-certificates >/dev/null
vhost=/etc/nginx/sites-available/ident006b-fixture.conf
sudo tee "$vhost" >/dev/null <<'NGINX'
server {
    listen 80;
    server_name ident-lab.test ident-alias.test;
    location / { return 404 "existing-http"; }
}
server {
    listen 443 ssl;
    server_name ident-lab.test ident-alias.test;
    ssl_certificate /etc/nginx/ident006b-fixture/server.crt;
    ssl_certificate_key /etc/nginx/ident006b-fixture/server.key;
    location / { return 200 "existing-site"; }
    location = /api/other { return 200 "existing-other"; }
    include /etc/nginx/ident006b-fixture/hidden.conf;
}
NGINX
# Existing conflicting location is intentionally hidden in an include. Initial
# publish must fail nginx -t and restore the original bytes.
echo 'location = /api/ident-bridge/v1/status { return 404; }' | sudo tee /etc/nginx/ident006b-fixture/hidden.conf >/dev/null
sudo ln -s "$vhost" /etc/nginx/sites-enabled/ident006b-fixture.conf
sudo nginx -t
sudo systemctl start nginx.service
sudo systemctl reload nginx.service
# All IDs and token are synthetic; never echo the raw token.
sudo python3 - <<'PY'
import hashlib,json,os,secrets,uuid
from pathlib import Path
os.umask(0o077)
p=Path("/root/ident006b-ci")
token=secrets.token_hex(32)
(p/"token").write_text(token)
(p/"config.json").write_text(json.dumps({"organization_id":str(uuid.uuid4()),"source_id":str(uuid.uuid4()),
 "stream_id":str(uuid.uuid4()),"source_timezone":"Asia/Novosibirsk","token_sha256":hashlib.sha256(token.encode()).hexdigest(),
 "max_store_bytes":32*1024*1024,"min_free_bytes":16*1024*1024}))
PY
host="$(hostname -s)"
machine="$(sha256sum /etc/machine-id | cut -d ' ' -f1)"
manifest="$(sudo sha256sum "$lab/source/deploy/INSTALL-MANIFEST.json" | cut -d ' ' -f1)"
base=(sudo /usr/bin/python3 -B "$lab/source/deploy/installer.py" --expected-host "$host" --machine-id-sha256 "$machine" --manifest-sha256 "$manifest")
# Prove read-only plan, host guard, and successful first install.
if sudo /usr/bin/python3 -B "$lab/source/deploy/installer.py" --expected-host definitely-wrong-host --machine-id-sha256 "$machine" --manifest-sha256 "$manifest" plan-stage --config "$lab/config.json" --wheelhouse "$lab/wheels"; then exit 1; fi
"${base[@]}" plan-stage --config "$lab/config.json" --wheelhouse "$lab/wheels"
test ! -e /opt/az-ident006b
"${base[@]}" --confirm IDENT-006B-STAGE stage --config "$lab/config.json" --wheelhouse "$lab/wheels"
sudo systemctl is-enabled --quiet az-ident006b.service
sudo systemctl is-enabled --quiet az-ident006b.socket
sudo -u www-data test ! -r /etc/az-ident006b/config.json
sudo -u www-data test ! -x /var/lib/az-ident006b/state
sudo -u az-ident-recv test ! -w /opt/az-ident006b/app/gateway.py
test "$(sudo stat -c '%U:%G:%a' /run/az-ident006b/receiver.sock)" = root:www-data:660
# No broad health service restart: only Nginx reload and new service.
sha="$(sha256sum "$vhost" | cut -d ' ' -f1)"
"${base[@]}" plan-https --vhost "$vhost" --https-host ident-lab.test --vhost-sha256 "$sha"
test "$(sha256sum "$vhost" | cut -d ' ' -f1)" = "$sha"
if "${base[@]}" --confirm IDENT-006B-PUBLISH publish --vhost "$vhost" --https-host ident-lab.test --vhost-sha256 "$sha"; then echo 'EXPECTED_CONFLICT_NOT_REJECTED'; exit 1; fi
test "$(sha256sum "$vhost" | cut -d ' ' -f1)" = "$sha"
test ! -e /etc/nginx/conf.d/az-ident006b-http.conf
test ! -e /etc/nginx/az-ident006b-locations.conf
sudo test ! -e /var/lib/az-ident006b/deploy/active.json
sudo test ! -e /var/lib/az-ident006b/deploy/pending.json
sudo nginx -t
echo 'NGINX_CONFLICT_ROLLBACK=PASS'
# Remove only the fixture conflict. The audited installer never edits includes.
echo '# fixture without conflict' | sudo tee /etc/nginx/ident006b-fixture/hidden.conf >/dev/null
sudo nginx -t
sudo systemctl reload nginx.service
"${base[@]}" --confirm IDENT-006B-PUBLISH publish --vhost "$vhost" --https-host ident-lab.test --vhost-sha256 "$sha"
sudo python3 "$lab/source/deploy/tests/lab_probe.py" check
pid="$(systemctl show -p MainPID --value az-ident006b.service)"
test "$pid" -gt 0
test "$(sudo readlink "/proc/$pid/ns/net")" != "$(sudo readlink /proc/1/ns/net)"
sudo systemctl kill --kill-whom=all --signal=SIGKILL az-ident006b.service
for attempt in $(seq 1 40); do
    next="$(systemctl show -p MainPID --value az-ident006b.service)"
    if [[ "$next" -gt 0 && "$next" != "$pid" ]] && systemctl is-active --quiet az-ident006b.service; then break; fi
    sleep 1
done
test "$next" -gt 0
test "$next" != "$pid"
sudo python3 "$lab/source/deploy/tests/lab_probe.py" after-restart
"${base[@]}" --confirm IDENT-006B-UNPUBLISH unpublish
test "$(sha256sum "$vhost" | cut -d ' ' -f1)" = "$sha"
test ! -e /etc/nginx/conf.d/az-ident006b-http.conf
test ! -e /etc/nginx/az-ident006b-locations.conf
sudo test -s /var/lib/az-ident006b/state/ident-shadow.sqlite3
sudo systemctl is-active --quiet az-baze-auth.service
echo 'IDENT_006B_DISPOSABLE_INSTALL=PASS; ROLLBACK=PASS; PRODUCTION=NONE; REAL_DATA=NONE'
