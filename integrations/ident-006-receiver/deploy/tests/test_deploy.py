"""No host mutations: unit tests for the guarded Nginx editor and deployment."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

DEPLOY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DEPLOY))
import nginx_edit as n
import installer as i


GOOD=b"""# keep comment and old routes
server { listen 80; server_name ident-lab.test; return 301 https://$host$request_uri; }
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ident-lab.test ident-alias.test;
    ssl_certificate /etc/ssl/example.pem;
    ssl_certificate_key /etc/ssl/example.key;
    location / { return 200 "old ; { } #"; }
    location /api/other { proxy_pass http://127.0.0.1:8001; }
}
"""


class PlanTests(unittest.TestCase):
    def test_exact_single_insertion_preserves_every_old_byte(self):
        result=n.plan(GOOD,"ident-lab.test")
        marker=b"\n    include /etc/nginx/az-ident006b-locations.conf;\n"
        self.assertEqual(result.count(marker),1)
        self.assertEqual(result.replace(marker,b"",1),GOOD)
        self.assertGreater(result.index(marker),result.index(b"listen 443 ssl"))
        self.assertEqual(n.parse(result.decode())[-1].children[-1].words,
                         ["include",n.LOCATIONS])

    def test_http_only_not_selected(self):
        with self.assertRaises(n.EditError):
            n.plan(b"server { listen 80; server_name ident-lab.test; }","ident-lab.test")

    def test_no_http_and_tls_on_same_block(self):
        raw=b"server { listen 80; listen 443 ssl; server_name ident-lab.test; }"
        with self.assertRaisesRegex(n.EditError,"MIXED_OR_UNSUPPORTED"):
            n.plan(raw,"ident-lab.test")

    def test_ambiguous_tls_servers(self):
        with self.assertRaisesRegex(n.EditError,"EXACTLY_ONE_TLS"):
            n.plan(GOOD+GOOD,"ident-lab.test")

    def test_existing_route_or_include_never_overwritten(self):
        for old in (b"location = /api/ident-bridge/v1/status { return 404; }",
                    b"include /etc/nginx/az-ident006b-locations.conf;"):
            raw=GOOD.replace(b"ssl_certificate /etc/ssl/example.pem;",old)
            with self.assertRaises(n.EditError):
                n.plan(raw,"ident-lab.test")

    def test_rewrite_or_return_at_server_level_stops(self):
        for old in (b"return 302 /login;",b"rewrite ^ /login last;",b"if ($host) { return 403; }",
                    b"error_page 401 /login;"):
            with self.assertRaisesRegex(n.EditError,"SERVER_LEVEL"):
                n.plan(GOOD.replace(b"ssl_certificate /etc/ssl/example.pem;",old),"ident-lab.test")

    def test_comment_quote_and_braced_variables(self):
        raw=GOOD.replace(b"location / {",b'# } server { ;\n    location / {').replace(
            b'proxy_pass http://127.0.0.1:8001;',b'proxy_set_header X-Test $' + b'{host}; proxy_pass http://127.0.0.1:8001;')
        self.assertIn(n.LOCATIONS.encode(),n.plan(raw,"ident-lab.test"))

    def test_crlf_and_nonascii_preserved(self):
        raw=("# original text \u0410\u0417\n".encode()+GOOD).replace(b"\n",b"\r\n")
        marker=b"\r\n    include /etc/nginx/az-ident006b-locations.conf;\r\n"
        self.assertEqual(n.plan(raw,"ident-lab.test").replace(marker,b"",1),raw)

    def test_malformed_and_host_injection_stop(self):
        for host in ("ident-lab.test; return 200","*.test","127.0.0.1\n","IDENT-LAB.TEST","localhost"):
            with self.assertRaises(n.EditError):
                n.plan(GOOD,host)
        for raw in (GOOD[:-4], b"\xff", b"server {\x00}"):
            with self.assertRaises(n.EditError):
                n.plan(raw,"ident-lab.test")

    def test_host_template_exact_and_only_two_routes(self):
        raw=(DEPLOY/"nginx-locations.conf").read_bytes()
        result=n.render_locations(raw,"ident-lab.test")
        self.assertEqual(result.count(b"ident-lab.test"),4)
        nodes=n.parse(result.decode())
        self.assertEqual([x.words for x in nodes],[["location","=",n.PATHS[0]],["location","=",n.PATHS[1]]])
        self.assertNotIn(b"__IDENT_HTTPS_HOST__",result)
        with self.assertRaises(n.EditError):
            n.render_locations(raw,"evil.test;")
        with self.assertRaises(n.EditError):
            n.render_locations(raw+b"__IDENT_HTTPS_HOST__","ident-lab.test")

    def test_proxy_strips_client_headers_and_limits_body(self):
        raw=(DEPLOY/"nginx-locations.conf").read_text()
        for block in n.parse(n.render_locations(raw.encode(),"ident-lab.test").decode()):
            directives=[x.words for x in block.children]
            for expected in (["proxy_pass_request_headers","off"],["proxy_set_header","X-Forwarded-Proto","https"],
                             ["proxy_set_header","Authorization","$http_authorization"],
                             ["client_max_body_size","512k"],["proxy_request_buffering","on"],
                             ["access_log","off"],["proxy_next_upstream","off"],["proxy_intercept_errors","off"]):
                self.assertIn(expected,directives)
            self.assertNotIn(["proxy_set_header","X-Forwarded-Proto","$http_x_forwarded_proto"],directives)


class SafetyTests(unittest.TestCase):
    def test_units_no_tcp_or_existing_database_access(self):
        svc=(DEPLOY/"az-ident006b.service").read_text()
        sock=(DEPLOY/"az-ident006b.socket").read_text()
        for line in ("User=az-ident-recv","Group=az-ident-recv","RestrictAddressFamilies=AF_UNIX",
                     "PrivateNetwork=true","ProtectSystem=strict","ReadWritePaths=/var/lib/az-ident006b/state",
                     "InaccessiblePaths=-/var/lib/az-baze -/etc/az-baze -/opt/az-baze-auth -/var/www",
                     "Restart=on-failure","WantedBy=multi-user.target","NoNewPrivileges=true"):
            self.assertIn(line,svc)
        self.assertIn("ListenStream=/run/az-ident006b/receiver.sock",sock)
        self.assertIn("SocketMode=0660",sock)
        self.assertIn("SocketGroup=www-data",sock)
        self.assertIn("WantedBy=sockets.target",sock)
        self.assertNotIn("ListenStream=0.0.0.0",sock)

    def test_invalid_duplicate_config_rejected(self):
        with self.assertRaises(i.Stop):
            i.json_object(b'{"x":1,"x":2}')
        with self.assertRaises(i.Stop):
            i.json_object(b'{"x":NaN}')
        with self.assertRaises(i.Stop):
            i.json_object(b'[]')

    def test_rollback_conflict_does_not_touch_external_file(self):
        record={"vhost":"/etc/nginx/sites-available/fixture","before_sha":"a","after_sha":"b"}
        with patch.object(i,"root_read",return_value=b"external change"), \
             patch.object(i,"replace_checked") as write,patch.object(i,"delete_checked") as delete:
            with self.assertRaisesRegex(i.Stop,"ROLLBACK_VHOST_CHANGED_EXTERNALLY"):
                i.rollback(record,Path("/unused"))
            write.assert_not_called()
            delete.assert_not_called()

    def test_rollback_rejects_modified_snippet_before_mutation(self):
        before=b"old"; after=b"new"
        record={"vhost":"/etc/nginx/sites-available/fixture","before_sha":i.digest(before),
                "after_sha":i.digest(after),"http_sha":i.digest(b"expected")}
        with patch.object(i,"root_read",side_effect=[after,b"external snippet"]), \
             patch.object(i,"absent",return_value=False),patch.object(i,"replace_checked") as write, \
             patch.object(i,"delete_checked") as delete:
            with self.assertRaisesRegex(i.Stop,"ROLLBACK_SNIPPET_CHANGED_EXTERNALLY"):
                i.rollback(record,Path("/unused"))
            write.assert_not_called()
            delete.assert_not_called()

    def test_audited_payload_contract_hashes_fixed(self):
        for name,sha in i.APP_SOURCE.items():
            self.assertEqual(i.digest((DEPLOY.parent/name).read_bytes()),sha)


if __name__=="__main__":
    unittest.main()
