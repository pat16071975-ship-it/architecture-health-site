# The launcher first verifies LISTEN_FDS. Gunicorn 23 recognizes the inherited
# listener; do not specify fd://3 as well (would duplicate that descriptor).
bind = "unix:/run/az-ident006b/receiver.sock"
workers = 1
threads = 2
worker_class = "gthread"
preload_app = True
backlog = 32
timeout = 35
graceful_timeout = 30
keepalive = 2
umask = 0o077
accesslog = None
errorlog = "-"
loglevel = "warning"
limit_request_line = 2048
limit_request_fields = 32
limit_request_field_size = 4096
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
# Only trusted local Unix-socket peers are allowed to reach this process.
forwarded_allow_ips = ""
