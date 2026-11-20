"""Regression tests use temporary roots and the exact old/new gallery fixtures."""
import ast
import base64
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import importlib.util
OPS = Path(__file__).resolve().parent.parent / "ops"
_SPEC = importlib.util.spec_from_file_location("apply_cases_gallery", OPS / "apply_cases_gallery_20261120.py")
app = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = app
_SPEC.loader.exec_module(app)


HERE = Path(__file__).resolve().parent
_FIXTURE_DIR = tempfile.TemporaryDirectory(prefix="az-gallery-fixture-")
ARCHIVE = Path(_FIXTURE_DIR.name) / "azmednsk-cases-gallery.pyz"
_PARTS = HERE.parent / "delivery" / "cases-gallery"
_MANIFEST = json.loads((_PARTS / "delivery-manifest.json").read_text())
ARCHIVE.write_bytes(b"".join((_PARTS / p["name"]).read_bytes() for p in _MANIFEST["parts"]))
REFERENCE = OPS / "apply_cases_20261119.py"


def old_files():
    tree = ast.parse(REFERENCE.read_text(encoding="utf-8"))
    encoded = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "ARCHIVE" for t in n.targets))
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode="r:gz") as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = app.load_bundle(ARCHIVE)
        cls.old = old_files()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="az-gallery-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "web"
        self.root.mkdir()
        self.backups = self.base / "backups"
        self.logs, self.requests = [], []
        files = {
            **self.old,
            "index.html": b"<!doctype html><h1>Original home</h1>",
            "sitemap.xml": b"<urlset><url><loc>https://azmednsk.ru/</loc></url></urlset>",
            "site.js": b"window.original=true;",
            "assets/global.css": b"body { color: #000; }",
            "osteopaty/index.html": b"<!doctype html><h1>Untouched neighbour</h1>",
        }
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)

    def tree(self):
        return {p.relative_to(self.root).as_posix():
                ("dir", p.stat().st_mode & 0o777) if p.is_dir()
                else ("file", p.read_bytes(), p.stat().st_mode & 0o777)
                for p in self.root.rglob("*")}

    def reader(self, path):
        self.requests.append(path)
        target = self.root / path.lstrip("/")
        if path.endswith("/"):
            target = target / "index.html"
        return target.read_bytes()

    def run_deploy(self, **kwargs):
        options = {"apply": True, "fetcher": self.reader, "log": self.logs.append}
        options.update(kwargs)
        return app.deploy(ARCHIVE, self.root, self.backups, **options)

    def test_archive_allowlist_and_old_baseline_are_exact(self):
        self.assertEqual(set(self.bundle.files), app.ALLOWED_PATHS)
        self.assertEqual(len(self.bundle.files), 33)
        self.assertEqual(len(app.HTML_PATHS), 11)
        self.assertEqual(len(app.ASSET_PATHS), 22)
        for name in app.HTML_PATHS:
            self.assertEqual(app.sha(self.old["cases/" + name]), self.bundle.hashes[name]["old"])
        self.assertEqual(app.sha(ARCHIVE.read_bytes()), app.ARCHIVE_SHA256)

    def test_check_only_has_no_writes_and_checks_all_11_https_pages(self):
        before = self.tree()
        result = self.run_deploy(apply=False)
        self.assertEqual(result["status"], "check_only")
        self.assertEqual(result["pending"], 33)
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.backups.exists())
        self.assertEqual(set(self.requests), {app.public_path(n) for n in app.HTML_PATHS})

    def test_apply_assets_first_backup_neighbors_and_idempotence(self):
        neighbors = app.snapshot_neighbors(self.root)
        order = []
        result = self.run_deploy(fault=lambda event, name: order.append(name) if event == "after_write" else None)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(set(order[:22]), app.ASSET_PATHS)
        self.assertEqual(set(order[22:]), app.HTML_PATHS)
        for name in app.ALLOWED_PATHS:
            self.assertEqual((self.root / "cases" / name).read_bytes(), self.bundle.files[name])
        self.assertEqual(app.snapshot_neighbors(self.root), neighbors)
        backup = Path(result["backup"])
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "applied")
        self.assertEqual(set(manifest["new_files"]), app.ASSET_PATHS)
        self.assertEqual(len(list((backup / "files").rglob("index.html"))), 11)
        self.assertEqual(app.current_hash(backup / "rollback.py"), manifest["rollback_script_sha256"])
        for name in app.HTML_PATHS:
            self.assertEqual((backup / "files" / name).read_bytes(), self.old["cases/" + name])
        before = self.tree()
        again = self.run_deploy()
        self.assertEqual(again["status"], "already_installed")
        self.assertEqual(self.tree(), before)
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_local_drift_rejected_before_backup_or_first_write(self):
        name = sorted(app.HTML_PATHS)[-1]
        (self.root / "cases" / name).write_bytes(b"edited by another deployment")
        before = self.tree()
        with self.assertRaisesRegex(app.DeployError, "old/new"):
            self.run_deploy()
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.requests, [])

    def test_https_drift_rejected_before_backup_or_first_write(self):
        before = self.tree()
        with self.assertRaisesRegex(app.DeployError, "HTTPS"):
            self.run_deploy(fetcher=lambda path: b"different vhost or cache")
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.backups.exists())

    def test_drift_during_https_rejected_before_backup(self):
        before = self.tree()
        changed = self.root / "cases" / sorted(app.ASSET_PATHS)[0]
        changed.parent.mkdir(parents=True)
        def reader(path):
            body = self.reader(path)
            changed.write_bytes(b"concurrent asset")
            return body
        with self.assertRaisesRegex(app.DeployError, "HTTPS preflight"):
            self.run_deploy(fetcher=reader)
        self.assertFalse(self.backups.exists())
        for name in app.HTML_PATHS:
            self.assertEqual((self.root / "cases" / name).read_bytes(), before["cases/" + name][1])

    def test_fault_after_first_html_restores_complete_original_tree(self):
        before = self.tree()
        def fault(event, name):
            if event == "after_write" and name in app.HTML_PATHS:
                raise RuntimeError("injected after first HTML")
        with self.assertRaisesRegex(app.DeployError, "восстановлены"):
            self.run_deploy(fault=fault)
        self.assertEqual(self.tree(), before)
        backup = next(self.backups.iterdir())
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "rolled_back")

    def test_fault_after_atomic_replace_before_return_is_rolled_back(self):
        before = self.tree()
        original = app.atomic_write
        tripped = False
        def fault_after_commit(path, data, expected, metadata):
            nonlocal tripped
            original(path, data, expected, metadata)
            if self.root in path.parents and path.suffix == ".webp" and not tripped:
                tripped = True
                raise OSError("injected post-rename fsync failure")
        with patch.object(app, "atomic_write", fault_after_commit):
            with self.assertRaisesRegex(app.DeployError, "восстановлены"):
                self.run_deploy()
        self.assertTrue(tripped)
        self.assertEqual(self.tree(), before)

    def test_post_https_failure_rolls_back_without_touching_neighbors(self):
        before = self.tree()
        def reader(path):
            if path.endswith(".webp"):
                return b"stale post-publication asset"
            return self.reader(path)
        with self.assertRaisesRegex(app.DeployError, "восстановлены"):
            self.run_deploy(fetcher=reader)
        self.assertEqual(self.tree(), before)

    def test_manual_rollback_script_is_standalone_and_idempotent(self):
        before = self.tree()
        result = self.run_deploy()
        script = Path(result["backup"]) / "rollback.py"
        for attempt in range(2):
            command = subprocess.run([sys.executable, str(script)], cwd=self.base,
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            self.assertIn("ROLLBACK=PASS", command.stdout)
            self.assertEqual(self.tree(), before)

    def test_manual_rollback_drift_guard_is_checked_before_any_restore(self):
        result = self.run_deploy()
        (self.root / "cases" / sorted(app.HTML_PATHS)[-1]).write_bytes(b"later owner change")
        before = self.tree()
        with self.assertRaisesRegex(app.DeployError, "current"):
            app.rollback_backup(Path(result["backup"]), log=self.logs.append)
        self.assertEqual(self.tree(), before)

    def test_resume_partial_new_state_and_rollback_to_that_exact_state(self):
        for name in (sorted(app.HTML_PATHS)[0], sorted(app.ASSET_PATHS)[0]):
            path = self.root / "cases" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.bundle.files[name])
        before = self.tree()
        result = self.run_deploy()
        self.assertEqual(result["pending"], 31)
        app.rollback_backup(Path(result["backup"]), log=self.logs.append)
        self.assertEqual(self.tree(), before)

    def test_symlink_target_is_rejected_before_writes(self):
        target = self.root / "cases" / sorted(app.HTML_PATHS)[0]
        outside = self.base / "outside.html"
        outside.write_bytes(target.read_bytes())
        expected = outside.read_bytes()
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaisesRegex(app.DeployError, "Символическая"):
            self.run_deploy()
        self.assertEqual(outside.read_bytes(), expected)
        self.assertFalse(self.backups.exists())

    def test_corrupt_archive_is_rejected(self):
        archive = self.base / "corrupt.pyz"
        archive.write_bytes(ARCHIVE.read_bytes() + b"unexpected suffix")
        with self.assertRaisesRegex(app.DeployError, "SHA256"):
            app.deploy(archive, self.root, self.backups, apply=True, fetcher=self.reader)
        self.assertFalse(self.backups.exists())

    def test_xattrs_survive_apply_and_manual_rollback(self):
        name = sorted(app.HTML_PATHS)[0]
        target = self.root / "cases" / name
        original = target.read_bytes()
        value = b"preserve-this-attribute\x00\xff"
        os.setxattr(target, "user.gallery_review", value)
        target.chmod(0o640)
        result = self.run_deploy()
        self.assertEqual(target.read_bytes(), self.bundle.files[name])
        self.assertEqual(os.getxattr(target, "user.gallery_review"), value)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        app.rollback_backup(Path(result["backup"]), log=self.logs.append)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(os.getxattr(target, "user.gallery_review"), value)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_xattr_restore_failure_never_replaces_original_and_rolls_back_assets(self):
        target = self.root / "cases" / sorted(app.HTML_PATHS)[0]
        os.setxattr(target, "user.gallery_review", b"keep-original")
        before = self.tree()
        with patch.object(os, "setxattr", side_effect=PermissionError("injected xattr denial")):
            with self.assertRaisesRegex(app.DeployError, "восстановлены.*extended attributes/ACL"):
                self.run_deploy()
        self.assertEqual(self.tree(), before)
        self.assertEqual(os.getxattr(target, "user.gallery_review"), b"keep-original")

    def test_manual_rollback_metadata_drift_guard_precedes_all_restores(self):
        result = self.run_deploy()
        target = self.root / "cases" / sorted(app.HTML_PATHS)[-1]
        os.setxattr(target, "user.gallery_review", b"later-owner-metadata")
        before = self.tree()
        with self.assertRaisesRegex(app.DeployError, "метаданные/ACL"):
            app.rollback_backup(Path(result["backup"]), log=self.logs.append)
        self.assertEqual(self.tree(), before)
        self.assertEqual(os.getxattr(target, "user.gallery_review"), b"later-owner-metadata")


class NginxTests(unittest.TestCase):
    @staticmethod
    def vhost(body):
        return 'http { server { listen 443 ssl; server_name azmednsk.ru; root /one; ' + body + ' } }'

    def test_selects_https_root_expands_includes_and_ignores_unrelated_proxy(self):
        config = '''# configuration file /etc/nginx/nginx.conf:
events { worker_connections 1024; }
http { include /etc/nginx/conf.d/*.conf; }
# configuration file /etc/nginx/conf.d/az.conf:
server { listen 80; server_name azmednsk.ru www.azmednsk.ru; return 301 https://azmednsk.ru$request_uri; }
server {
    listen 443 ssl; listen [::]:443 ssl;
    server_name azmednsk.ru www.azmednsk.ru;
    root /var/www/azmednsk-preview;
    include /etc/nginx/snippets/az-static.conf;
    location /api/ { proxy_pass http://127.0.0.1:8000; }
}
# configuration file /etc/nginx/snippets/az-static.conf:
location / { try_files $uri $uri/ =404; }
location ~* \\.(webp|css|js)$ { expires 30d; }
'''
        self.assertEqual(app.parse_nginx_root(config), Path("/var/www/azmednsk-preview"))

    def test_http_root_inheritance(self):
        config = 'http { root "/var/www/azmednsk-preview"; server { listen 443 ssl; server_name azmednsk.ru; } }'
        self.assertEqual(app.parse_nginx_root(config), Path("/var/www/azmednsk-preview"))

    def test_ambiguous_https_vhosts_rejected(self):
        config = 'http { server { listen 443 ssl; server_name azmednsk.ru; root /one; } server { listen 443 ssl; server_name azmednsk.ru; root /two; } }'
        with self.assertRaisesRegex(app.DeployError, "HTTPS-vhost: 2"):
            app.parse_nginx_root(config)

    def test_case_alias_and_regex_asset_root_rejected(self):
        for location in ('location /cases/ { alias /other/; }',
                         'location ~* \\.webp$ { root /other; }',
                         'location / { proxy_pass http://localhost; }'):
            config = 'http { server { listen 443 ssl; server_name azmednsk.ru; root /one; ' + location + ' } }'
            with self.assertRaises(app.DeployError):
                app.parse_nginx_root(config)

    def test_missing_include_and_dynamic_root_rejected(self):
        missing = '''# configuration file /etc/nginx/nginx.conf:
http { include /etc/nginx/missing.conf; }
'''
        with self.assertRaisesRegex(app.DeployError, "include"):
            app.parse_nginx_root(missing)
        with self.assertRaisesRegex(app.DeployError, "Динамический"):
            app.parse_nginx_root('server { listen 443 ssl; server_name azmednsk.ru; root /www/$host; }')

    def test_similarly_named_domain_is_not_selected(self):
        config = 'server { listen 443 ssl; server_name other-azmednsk.ru; root /wrong; }'
        with self.assertRaisesRegex(app.DeployError, "HTTPS-vhost: 0"):
            app.parse_nginx_root(config)

    def test_cli_has_no_root_or_https_bypass(self):
        with redirect_stderr(io.StringIO()):
            for override in ('--root', '--skip-https', '--archive-sha'):
                with self.assertRaises(SystemExit) as error:
                    app.main(['--archive', str(ARCHIVE), '--check-only', override, '/tmp'])
                self.assertEqual(error.exception.code, 2)

    def test_www_redirect_and_non_get_method_guard_are_false_for_our_requests(self):
        body = '''
            if ($host = www.azmednsk.ru) { return 301 https://azmednsk.ru$request_uri; }
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            location / { try_files $uri $uri/ =404; }
        '''
        self.assertEqual(app.parse_nginx_root(self.vhost(body)), Path('/one'))

    def test_known_true_routing_conditions_are_rejected(self):
        for condition in ('$host = azmednsk.ru', '$host != www.azmednsk.ru',
                          '$request_method = GET', '$request_method ~* ^get$'):
            with self.subTest(condition=condition):
                with self.assertRaisesRegex(app.DeployError, 'return'):
                    app.parse_nginx_root(self.vhost('if (' + condition + ') { return 301 /other; }'))

    def test_known_false_eq_ne_and_case_insensitive_regex_are_skipped(self):
        for condition in ('$host != azmednsk.ru', '$request_method = POST',
                          '$request_method !~* ^get$', '${host} = www.azmednsk.ru'):
            with self.subTest(condition=condition):
                self.assertEqual(app.parse_nginx_root(self.vhost(
                    'if (' + condition + ') { return 405; }')), Path('/one'))

    def test_unknown_condition_with_routing_is_fail_closed(self):
        for condition in ('$http_x_unknown = 1', '$host = $http_host',
                          '$request_method ~ [unsupported', '-f $request_filename'):
            with self.subTest(condition=condition):
                with self.assertRaisesRegex(app.DeployError, 'Неоднозначное if'):
                    app.parse_nginx_root(self.vhost('if (' + condition + ') { return 301 /other; }'))
        self.assertEqual(app.parse_nginx_root(self.vhost(
            'if ($http_x_unknown = 1) { add_header X-Diagnostic yes; }')), Path('/one'))

    def test_braced_variable_in_other_vhost_does_not_corrupt_parser(self):
        config = '''http {
            server { listen 443 ssl; server_name other.example; root /other/${host}; }
            server { listen 443 ssl; server_name azmednsk.ru; root /one; }
        }'''
        self.assertEqual(app.parse_nginx_root(config), Path('/one'))
        with self.assertRaisesRegex(app.DeployError, 'Динамический'):
            app.parse_nginx_root('server { listen 443 ssl; server_name azmednsk.ru; root /other/${host}; }')


if __name__ == "__main__":
    unittest.main(verbosity=2)
