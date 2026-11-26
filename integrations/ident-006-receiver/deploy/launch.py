"""Require systemd's one private AF_UNIX listener. Never fall back to TCP."""
import os
import socket
import stat
import sys
import grp

SOCKET = "/run/az-ident006b/receiver.sock"
APP = "/opt/az-ident006b/app"


def main():
    try:
        if os.geteuid() == 0 or os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
            raise RuntimeError("SOCKET_ACTIVATION_REQUIRED")
        with socket.socket(fileno=os.dup(3)) as s:
            if s.family != socket.AF_UNIX or s.type != socket.SOCK_STREAM or s.getsockname() != SOCKET or not s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
                raise RuntimeError("UNEXPECTED_LISTENER")
        st = os.lstat(SOCKET)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != 0 or st.st_gid != grp.getgrnam("www-data").gr_gid or stat.S_IMODE(st.st_mode) != 0o660:
            raise RuntimeError("SOCKET_PERMISSIONS")
        os.set_inheritable(3, True)
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LISTEN_PID": str(os.getpid()),
            "LISTEN_FDS": "1",
            "IDENT_RECEIVER_CONFIG": "/etc/az-ident006b/config.json",
            "IDENT_RECEIVER_STATE_DIR": "/var/lib/az-ident006b/state",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        os.execve(sys.executable, [sys.executable, "-I", "-B", "-m", "gunicorn",
                  "--chdir", APP, "--config", APP+"/gunicorn_conf.py", "gateway:create_app()"], env)
    except (OSError, RuntimeError, KeyError, ValueError):
        print("STOP=PRIVATE_SOCKET_STARTUP", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
