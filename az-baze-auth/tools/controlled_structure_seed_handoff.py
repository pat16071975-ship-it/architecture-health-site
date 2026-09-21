#!/usr/bin/env python3
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

AUDITED_CONTROLLER_COMMIT = "23ece9e78ca2cc9571da1599481d2c246edf0a63"
AUDITED_CONTROLLER_BLOB = "be38797d72517d00b23f48058a4914bf620e5048"
CONTROLLER_PATH = "az-baze-auth/tools/controlled_structure_seed.py"


class HandoffError(RuntimeError):
    pass


def git_blob_sha(data):
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def controller_url():
    return (
        "https://raw.githubusercontent.com/"
        "pat16071975-ship-it/architecture-health-site/"
        f"{AUDITED_CONTROLLER_COMMIT}/{CONTROLLER_PATH}"
    )


def download_controller():
    request = urllib.request.Request(
        controller_url(),
        headers={"User-Agent": "AZ-BAZE-Controlled-Structure-Seed-Handoff/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except Exception as exc:
        raise HandoffError(f"cannot download audited seed controller: {exc}") from exc


def verify_controller_bytes(data):
    actual = git_blob_sha(data)
    if actual != AUDITED_CONTROLLER_BLOB:
        raise HandoffError(
            "seed controller Git blob mismatch: "
            f"actual={actual} expected={AUDITED_CONTROLLER_BLOB}"
        )
    try:
        compile(data, CONTROLLER_PATH, "exec")
    except Exception as exc:
        raise HandoffError(f"seed controller syntax validation failed: {exc}") from exc
    return actual


def main():
    data = download_controller()
    verified = verify_controller_bytes(data)

    print(f"AUDITED_SEED_CONTROLLER_COMMIT={AUDITED_CONTROLLER_COMMIT}", flush=True)
    print(f"AUDITED_SEED_CONTROLLER_BLOB={verified}", flush=True)

    with tempfile.TemporaryDirectory(prefix="az-structure-seed-handoff-") as temp:
        target = Path(temp) / "controlled_structure_seed.py"
        target.write_bytes(data)
        os.chmod(target, 0o700)
        os.execv(
            sys.executable,
            [sys.executable, str(target), *sys.argv[1:]],
        )


if __name__ == "__main__":
    main()
