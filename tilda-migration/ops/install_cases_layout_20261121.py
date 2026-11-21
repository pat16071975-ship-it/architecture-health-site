"""Entry point of the cases viewer release zipapp."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import socket
import sys
import time
from urllib.error import URLError
import zipfile

import apply_cases_gallery_20261120 as installer


def fetch_with_dns_retry(path):
    delays = (2, 5, 10)
    for attempt in range(len(delays) + 1):
        try:
            return installer.fetch_public(path)
        except URLError as error:
            reason = error.reason
            if not isinstance(reason, socket.gaierror) or reason.errno != socket.EAI_AGAIN:
                raise
            if attempt == len(delays):
                raise
            delay = delays[attempt]
            print(f"DNS_RETRY={attempt + 1}; WAIT={delay}s; PATH={path}", flush=True)
            time.sleep(delay)


def run():
    artifact = Path(sys.argv[0]).resolve()
    # The existing rollback implementation saves its own source. Save the complete
    # executable so rollback remains offline and uses this release's identity.
    installer.HTML_PATHS = installer.HTML_PATHS | {"index.html"}
    installer.ALLOWED_PATHS = installer.HTML_PATHS | installer.ASSET_PATHS
    installer.__file__ = str(artifact)
    installer.ARCHIVE_SHA256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if artifact.name == "rollback.py":
        installer.rollback_backup(artifact.parent)
        return
    args = argparse.ArgumentParser()
    args.add_argument("--apply", action="store_true")
    options = args.parse_args()
    if options.apply and os.geteuid() != 0:
        raise installer.DeployError("Для установки требуется root")
    web_root = installer.discover_root()

    def load_release(path):
        with zipfile.ZipFile(path) as package:
            hashes = json.loads(package.read("hashes.json"))
            if set(hashes) != installer.ALLOWED_PATHS:
                raise installer.DeployError("Неверный состав пакета")
            files = {}
            for name in installer.HTML_PATHS:
                files[name] = package.read("pages/" + name)
                if installer.sha(files[name]) != hashes[name]["new"]:
                    raise installer.DeployError("Неверный SHA256 страницы " + name)
            # Media are already live. A missing or changed file blocks installation.
            for name in installer.ASSET_PATHS:
                target = installer.target_path(web_root / "cases", name)
                if installer.current_hash(target) != hashes[name]["new"]:
                    raise installer.DeployError("Изображение изменилось: " + name)
            return installer.Bundle(hashes, files)

    installer.load_bundle = load_release
    installer.deploy(artifact, web_root, Path("/var/backups/azmednsk"), apply=options.apply,
                     fetcher=fetch_with_dns_retry)


if __name__ == "__main__":
    try:
        run()
    except (Exception, KeyboardInterrupt) as error:
        sys.exit("STOP=" + str(error))
