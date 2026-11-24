#!/usr/bin/env python3
"""Install or roll back the approved Besedina profile and certificate gallery."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import socket
import time
import urllib.error
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


ARCHIVE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ORIGIN = "https://azmednsk.ru"
HASHES = {'index.html': {'old': 'd518d2fcb8a75c1ffa5d50d0c7c842f64ea7e8717170e6d37e95864e58db0d28', 'new': '246af4f5ceb6f67f49687598c1f8a040f939ac9b85c69778657e0210f8d3ea36'}}
HTML_PATHS = frozenset(("index.html",))
ALLOWED_PATHS = frozenset(HASHES)
ASSET_PATHS = ALLOWED_PATHS - HTML_PATHS
REQUIRED_NEIGHBORS = ("index.html", "team/index.html", "sitemap.xml")
MAX_FILE = 20 * 1024 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
MAX_NEIGHBOR_BYTES = 2 * 1024 * 1024 * 1024
MAX_NEIGHBOR_FILES = 50000
HEX = re.compile(r"[a-f0-9]{64}\Z")


class DeployError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeployError("Повторяющийся ключ JSON: " + key)
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


@dataclass(frozen=True)
class Bundle:
    hashes: dict
    files: dict


def retry_dns(read):
    for attempt in range(4):
        try:
            return read()
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, socket.gaierror) or exc.reason.errno != socket.EAI_AGAIN or attempt == 3:
                raise
            time.sleep((2, 5, 10)[attempt])


def fetch_live(path):
    try:
        return retry_dns(lambda: fetch_public(path))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def load_bundle(_):
    import zlib
    page = zlib.decompress(base64.b64decode('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'))
    if sha(page) != HASHES["index.html"]["new"]:
        raise DeployError("Ошибка проверки страницы")
    return Bundle(HASHES, {"index.html": page})

@dataclass(frozen=True)
class Directive:
    words: tuple
    children: tuple | None = None


def nginx_tokens(text):
    result, word = [], []
    i, quote = 0, None
    while i < len(text):
        char = text[i]
        if char == "$" and text[i:i + 2] == "${":
            end = text.find("}", i + 2)
            if end < 0 or not re.fullmatch(r"[A-Za-z0-9_]+", text[i + 2:end]):
                raise DeployError("Некорректная braced variable nginx")
            word.append(text[i:end + 1])
            i = end + 1
            continue
        if char == "\\":
            if i + 1 >= len(text):
                raise DeployError("Оборванная escape-последовательность nginx")
            following = text[i + 1]
            # Preserve regex escapes; only unescape whitespace and quoted delimiters.
            if following.isspace() or following in "\\\"'{};#":
                word.append(following)
            else:
                word.extend((char, following))
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "\"'":
            quote = char
        elif char == "#":
            if word:
                result.append("".join(word))
                word = []
            stop = text.find("\n", i)
            i = len(text) if stop < 0 else stop
            continue
        elif char.isspace() or char in "{};":
            if word:
                result.append("".join(word))
                word = []
            if char in "{};":
                result.append(char)
        else:
            word.append(char)
        i += 1
    if quote:
        raise DeployError("Незакрытая строка nginx")
    if word:
        result.append("".join(word))
    return result


def nginx_parse(text):
    tokens = nginx_tokens(text)
    index = 0

    def block(nested=False):
        nonlocal index
        nodes, words = [], []
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "}":
                if not nested or words:
                    raise DeployError("Неоднозначная структура nginx")
                return tuple(nodes)
            if token == ";":
                if not words:
                    raise DeployError("Пустая директива nginx")
                nodes.append(Directive(tuple(words)))
                words = []
            elif token == "{":
                if not words:
                    raise DeployError("Блок nginx без имени")
                nodes.append(Directive(tuple(words), block(True)))
                words = []
            else:
                words.append(token)
        if nested or words:
            raise DeployError("Незавершённая структура nginx")
        return tuple(nodes)

    return block()


def nginx_expanded(dump):
    markers = list(re.finditer(r"^# configuration file (.+):\s*$", dump, re.M))
    if not markers:
        return nginx_parse(dump)
    files = {}
    order = []
    for position, marker in enumerate(markers):
        name = marker.group(1)
        if name in files or not name.startswith("/"):
            raise DeployError("Повторяющийся или относительный источник nginx -T")
        end = markers[position + 1].start() if position + 1 < len(markers) else len(dump)
        files[name] = nginx_parse(dump[marker.end():end])
        order.append(name)
    prefix = str(PurePosixPath(order[0]).parent)

    def expand(nodes, stack):
        if len(stack) > 30:
            raise DeployError("Слишком глубокие include nginx")
        result = []
        for node in nodes:
            if node.words[0] == "include":
                if len(node.words) != 2 or node.children is not None or "$" in node.words[1]:
                    raise DeployError("Неподдерживаемый include nginx")
                pattern = node.words[1]
                if not pattern.startswith("/"):
                    pattern = prefix + "/" + pattern
                matches = sorted(name for name in files if fnmatch.fnmatchcase(name, pattern))
                if not matches and not any(c in pattern for c in "*?["):
                    raise DeployError("nginx -T не содержит include: " + pattern)
                for name in matches:
                    if name in stack:
                        raise DeployError("Циклический include nginx")
                    result.extend(expand(files[name], stack + (name,)))
            else:
                children = expand(node.children, stack) if node.children is not None else None
                result.append(Directive(node.words, children))
        return tuple(result)

    return expand(files[order[0]], (order[0],))


def public_path(name):
    path = "/uslugi/functional_dentistry/" + name
    return path[:-len("index.html")] if name in HTML_PATHS else path


def location_applies(words, paths):
    args = words[1:]
    if len(args) == 1:
        return any(p.startswith(args[0]) for p in paths)
    if len(args) != 2:
        raise DeployError("Неоднозначная location nginx")
    modifier, pattern = args
    if modifier == "=":
        return pattern in paths
    if modifier == "^~":
        return any(p.startswith(pattern) for p in paths)
    if modifier in ("~", "~*"):
        try:
            return any(re.search(pattern, p, re.I if modifier == "~*" else 0) for p in paths)
        except re.error as exc:
            raise DeployError("Не удалось безопасно разобрать regex location") from exc
    raise DeployError("Неподдерживаемый модификатор location nginx")


ROUTING_DIRECTIVES = frozenset(("root", "alias", "proxy_pass", "fastcgi_pass", "uwsgi_pass",
                                "scgi_pass", "grpc_pass", "rewrite", "return"))


def known_nginx_condition(words):
    """Evaluate only simple conditions for the exact apex GET requests we make.

    Unknown operands and unsupported regular expressions return None. They must
    never be assumed false when their body can change routing.
    """
    expression = " ".join(words[1:]).strip()
    if not (expression.startswith("(") and expression.endswith(")")):
        return None
    match = re.fullmatch(
        r"\s*(\$(?:host|request_method)|\$\{(?:host|request_method)\})\s+"
        r"(=|!=|~\*?|!~\*?)\s+(.+?)\s*", expression[1:-1], re.S)
    if not match:
        return None
    variable, operator, rhs = match.groups()
    name = variable[2:-1] if variable.startswith("${") else variable[1:]
    value = {"host": "azmednsk.ru", "request_method": "GET"}[name]
    if operator in ("=", "!="):
        if "$" in rhs:
            return None
        equal = value == rhs
        return equal if operator == "=" else not equal
    # A trailing regex '$' is an anchor; '$name' may be a dynamic operand.
    if len(rhs) > 256 or re.search(r"\$(?:[A-Za-z_]|\{)", rhs):
        return None
    try:
        matched = re.search(rhs, value, re.I if operator.endswith("*") else 0) is not None
    except re.error:
        return None
    return not matched if operator.startswith("!") else matched


def contains_routing(nodes):
    return any(n.words[0] in ROUTING_DIRECTIVES or
               (n.children is not None and contains_routing(n.children)) for n in nodes)


def parse_nginx_root(dump):
    nodes = nginx_expanded(dump)
    http = [n for n in nodes if n.words == ("http",) and n.children is not None]
    if len(http) > 1:
        raise DeployError("Несколько http-контекстов nginx")
    context = http[0].children if http else nodes
    inherited = [n.words[1:] for n in context if n.words[0] == "root"]
    candidates = []
    for server in context:
        if server.words != ("server",) or server.children is None:
            continue
        names = [v for n in server.children if n.words[0] == "server_name" for v in n.words[1:]]
        listens = [n.words[1:] for n in server.children if n.words[0] == "listen"]
        https = any(args and (args[0] == "443" or args[0].endswith(":443")) for args in listens)
        if "azmednsk.ru" not in names or not https:
            continue
        roots = [n.words[1:] for n in server.children if n.words[0] == "root"] or inherited
        if len(roots) != 1 or len(roots[0]) != 1:
            raise DeployError("HTTPS-vhost не имеет одного однозначного root")
        root = roots[0][0]
        if not root.startswith("/") or "$" in root or ".." in PurePosixPath(root).parts:
            raise DeployError("Динамический или относительный root nginx")
        paths = {public_path(p) for p in ALLOWED_PATHS} | {"/uslugi/functional_dentistry/"}

        def validate(nodes_to_check):
            for node in nodes_to_check:
                key = node.words[0]
                if key == "location":
                    if node.words[1:2] and node.words[1].startswith("@"):
                        continue
                    if location_applies(node.words, paths):
                        validate(node.children or ())
                elif key == "if":
                    applies = known_nginx_condition(node.words)
                    if applies is False:
                        continue
                    if applies is None and contains_routing(node.children or ()):
                        raise DeployError("Неоднозначное if nginx с изменением маршрутизации")
                    validate(node.children or ())
                elif key == "root":
                    if node.words[1:] != (root,):
                        raise DeployError("Root /cases/ переопределён в location")
                elif key in ("alias", "proxy_pass", "fastcgi_pass", "uwsgi_pass", "scgi_pass",
                             "grpc_pass", "rewrite", "return"):
                    raise DeployError("Маршрут /cases/ содержит " + key + "; требуется адресная проверка")
                elif node.children is not None:
                    validate(node.children)
        validate(server.children)
        candidates.append(Path(root))
    if len(candidates) != 1:
        raise DeployError("Для azmednsk.ru:443 найдено HTTPS-vhost: " + str(len(candidates)))
    return candidates[0]


def discover_root():
    try:
        result = subprocess.run(["nginx", "-T"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployError("Не удалось выполнить nginx -T") from exc
    if result.returncode or "conflicting server name" in result.stderr.lower():
        raise DeployError("nginx -T завершился ошибкой или конфликтом server_name")
    return parse_nginx_root(result.stdout)


def safe_absolute(path, may_be_missing=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise DeployError("Требуется абсолютный путь без '..'")
    current = Path(path.anchor)
    missing = False
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise DeployError("Символическая ссылка в пути: " + str(current))
        if not current.exists():
            missing = True
        elif missing:
            raise DeployError("Путь изменился во время проверки")
    if missing and not may_be_missing:
        raise DeployError("Путь отсутствует: " + str(path))
    return path


def target_path(cases_root, name):
    if name not in ALLOWED_PATHS:
        raise DeployError(f"Путь вне разрешённых {len(ALLOWED_PATHS)} файлов: " + str(name))
    return safe_absolute(cases_root / name, may_be_missing=True)


def read_regular(path, missing=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if missing:
            return None, None
        raise DeployError("Отсутствует файл: " + str(path))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DeployError("Не обычный файл: " + str(path))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read()
        return data, {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
                      "xattrs": capture_xattrs(fd)}
    finally:
        os.close(fd)


def capture_xattrs(fd):
    try:
        return {name: base64.b64encode(os.getxattr(fd, name)).decode("ascii")
                for name in sorted(os.listxattr(fd))}
    except (OSError, AttributeError) as exc:
        raise DeployError("Не удалось прочитать extended attributes/ACL; запись запрещена") from exc


def restore_xattrs(fd, expected):
    try:
        for name in set(os.listxattr(fd)) - set(expected):
            os.removexattr(fd, name)
        for name, encoded in expected.items():
            os.setxattr(fd, name, base64.b64decode(encoded, validate=True))
        if capture_xattrs(fd) != expected:
            raise DeployError("Проверка extended attributes/ACL не пройдена")
    except (OSError, AttributeError, ValueError) as exc:
        raise DeployError("Не удалось сохранить extended attributes/ACL; замена запрещена") from exc


def current_hash(path):
    data, _ = read_regular(path, missing=True)
    return sha(data) if data is not None else None


def inspect_targets(cases_root, bundle):
    states = {}
    for name in sorted(ALLOWED_PATHS):
        path = target_path(cases_root, name)
        data, metadata = read_regular(path, missing=True)
        digest = sha(data) if data is not None else None
        if digest not in (bundle.hashes[name]["old"], bundle.hashes[name]["new"]):
            raise DeployError("Файл отличается от old/new manifest: " + name)
        states[name] = {"data": data, "metadata": metadata, "sha256": digest,
                        "pending": digest != bundle.hashes[name]["new"]}
    return states


def snapshot_neighbors(web_root):
    for name in REQUIRED_NEIGHBORS:
        path = safe_absolute(web_root / name)
        if not path.is_file():
            raise DeployError("Нет обязательного соседнего файла: " + name)
    excluded = {"uslugi/functional_dentistry/" + p for p in ALLOWED_PATHS}
    result, total = {}, 0
    for parent, directories, files in os.walk(web_root, followlinks=False):
        parent = Path(parent)
        for name in list(directories):
            path = parent / name
            if path.is_symlink():
                result[path.relative_to(web_root).as_posix()] = {"kind": "symlink", "target": os.readlink(path)}
                directories.remove(name)
        for name in sorted(files):
            path = parent / name
            rel = path.relative_to(web_root).as_posix()
            if rel in excluded:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[rel] = {"kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_NEIGHBOR_BYTES or len(result) >= MAX_NEIGHBOR_FILES:
                    raise DeployError("Защитный снимок превышает лимит; нужна адресная проверка root")
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[rel] = {"kind": "file", "sha256": digest.hexdigest(),
                               "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            else:
                raise DeployError("Специальный файл внутри web-root: " + rel)
    return result


def assert_neighbors(web_root, expected):
    actual = snapshot_neighbors(web_root)
    if actual != expected:
        changes = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        raise DeployError("Изменились соседние файлы: " + ", ".join(changes[:8]))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_public(path):
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(ORIGIN + path, headers={
        "User-Agent": "AZMEDNSK-Gallery-Verification/1.0", "Accept-Encoding": "identity",
        "Cache-Control": "no-cache", "Pragma": "no-cache",
    })
    with opener.open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != ORIGIN + path:
            raise DeployError("HTTPS вернул неожиданный статус или перенаправление")
        body = response.read(MAX_FILE + 1)
    if len(body) > MAX_FILE:
        raise DeployError("HTTPS ответ превышает лимит")
    return body


def verify_https(expected, fetcher):
    def verify(item):
        name, digest = item
        try:
            data = fetcher(public_path(name))
            if (None if data is None else sha(data)) != digest:
                raise DeployError("HTTPS hash не совпадает")
        except Exception as exc:
            raise DeployError("HTTPS проверка не пройдена: " + public_path(name) + "; " + str(exc)) from exc
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(verify, sorted(expected.items())))


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_parent(path):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    safe_absolute(parent)
    for folder in reversed(missing):
        folder.mkdir(mode=0o755)
        fsync_dir(folder.parent)


def atomic_write(path, data, expected, metadata):
    safe_absolute(path, may_be_missing=True)
    previous, previous_metadata = read_regular(path, missing=True)
    if (sha(previous) if previous is not None else None) != expected:
        raise DeployError("Файл изменился перед записью: " + str(path))
    if previous_metadata is not None and previous_metadata != metadata:
        raise DeployError("Метаданные/ACL изменились перед записью: " + str(path))
    ensure_parent(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".az-cases-gallery-", delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(data)
            out.flush()
            if os.geteuid() == 0:
                os.fchown(out.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(out.fileno(), metadata["mode"])
            if "xattrs" in metadata:
                restore_xattrs(out.fileno(), metadata["xattrs"])
            os.fsync(out.fileno())
            safe_absolute(path, may_be_missing=True)
            current, current_metadata = read_regular(path, missing=True)
            if ((sha(current) if current is not None else None) != expected or
                    current_metadata != previous_metadata):
                raise DeployError("Файл изменился во время подготовки записи: " + str(path))
            os.replace(temporary, path)
            fsync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def save_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    previous, metadata = read_regular(path, missing=True)
    if metadata is None:
        metadata = {"mode": 0o600, "uid": os.geteuid(), "gid": os.getegid()}
    atomic_write(path, data, sha(previous) if previous is not None else None, metadata)


def planned_directories(cases_root, states):
    missing = set()
    for name, state in states.items():
        if not state["pending"]:
            continue
        parent = target_path(cases_root, name).parent
        while parent != cases_root and not parent.exists():
            missing.add(parent.relative_to(cases_root).as_posix())
            parent = parent.parent
    return sorted(missing, key=lambda p: (p.count("/"), p))


def make_backup(web_root, backups, bundle, states, neighbors):
    safe_absolute(backups, may_be_missing=True)
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / ("functional-dentistry-portraits-" + stamp)
    backup.mkdir(mode=0o700)
    entries = {}
    for name, state in states.items():
        item = {**bundle.hashes[name], "before": state["sha256"],
                "pending": state["pending"], "metadata": state["metadata"]}
        if state["data"] is not None:
            saved = backup / "files" / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(saved, "xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(state["data"])
                stream.flush()
                os.fsync(stream.fileno())
            if current_hash(saved) != state["sha256"]:
                raise DeployError("Не прошла проверка резервной копии: " + name)
            item["backup_file"] = "files/" + name
        entries[name] = item
    source = Path(__file__).read_bytes()
    rollback = backup / "rollback.py"
    with open(rollback, "xb") as stream:
        os.fchmod(stream.fileno(), 0o700)
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    manifest = {
        "schema": 1, "archive_sha256": ARCHIVE_SHA256,
        "web_root": str(web_root), "cases_root": str(web_root / "uslugi/functional_dentistry"),
        "created_utc": stamp, "status": "prepared", "entries": entries,
        "new_files": sorted(n for n, s in states.items() if s["pending"] and s["data"] is None),
        "created_directories": planned_directories(web_root / "uslugi/functional_dentistry", states),
        "neighbors_before": neighbors, "rollback_script_sha256": sha(source),
    }
    save_json(backup / "manifest.json", manifest)
    for parent, directories, _ in os.walk(backup, topdown=False):
        fsync_dir(Path(parent))
    fsync_dir(backups)
    return backup, manifest


def load_rollback_manifest(backup):
    safe_absolute(backup)
    manifest = parse_json(read_regular(backup / "manifest.json")[0])
    if manifest.get("schema") != 1 or manifest.get("archive_sha256") != ARCHIVE_SHA256:
        raise DeployError("Неизвестный manifest резервной копии")
    if set(manifest.get("entries", {})) != ALLOWED_PATHS:
        raise DeployError(f"Rollback manifest выходит за {len(ALLOWED_PATHS)} разрешённых пути")
    web_root = safe_absolute(Path(manifest["web_root"]))
    if manifest.get("cases_root") != str(web_root / "uslugi/functional_dentistry"):
        raise DeployError("Несовпадение root в rollback manifest")
    if current_hash(backup / "rollback.py") != manifest.get("rollback_script_sha256"):
        raise DeployError("Rollback script повреждён")
    for name, item in manifest["entries"].items():
        if not isinstance(item.get("new"), str) or not HEX.fullmatch(item["new"]):
            raise DeployError("Неверный hash rollback manifest")
        if item.get("before") not in (item.get("old"), item["new"]):
            raise DeployError("Неверный исходный hash rollback manifest")
        if item.get("pending") != (item["before"] != item["new"]):
            raise DeployError("Неверный pending rollback manifest")
        if item["before"] is not None:
            if item.get("backup_file") != "files/" + name:
                raise DeployError("Неверный путь резервной копии")
            saved = safe_absolute(backup / item["backup_file"])
            if current_hash(saved) != item["before"]:
                raise DeployError("Hash резервной копии не совпадает: " + name)
    expected_new = sorted(n for n, e in manifest["entries"].items() if e["pending"] and e["before"] is None)
    if manifest.get("new_files") != expected_new:
        raise DeployError("Неверный список новых файлов rollback")
    possible_dirs = {p.as_posix() for n in ALLOWED_PATHS for p in PurePosixPath(n).parents if p.as_posix() != "."}
    if not set(manifest.get("created_directories", ())).issubset(possible_dirs):
        raise DeployError("Неверный список каталогов rollback")
    return manifest, web_root


def rollback_backup(backup, *, locked=False, log=print):
    backup = Path(backup)
    manifest, web_root = load_rollback_manifest(backup)
    root_fd = None
    if not locked:
        root_fd = os.open(web_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(root_fd, fcntl.LOCK_EX)
    try:
        cases_root = safe_absolute(web_root / "uslugi/functional_dentistry")
        neighbors = snapshot_neighbors(web_root)
        # Check every destination before restoring even the first file.
        for name, item in manifest["entries"].items():
            data, metadata = read_regular(target_path(cases_root, name), missing=True)
            actual = sha(data) if data is not None else None
            if actual not in (item["before"], item["new"]):
                raise DeployError("Rollback остановлен: current не равен saved/new: " + name)
            if not item["pending"] and actual != item["before"]:
                raise DeployError("Rollback остановлен: изменён нетронутый файл: " + name)
            if item["before"] is not None and metadata != item["metadata"]:
                raise DeployError("Rollback остановлен: метаданные/ACL изменились: " + name)
        order = sorted(manifest["entries"], key=lambda n: (n not in HTML_PATHS, n))
        for name in order:
            item = manifest["entries"][name]
            path = target_path(cases_root, name)
            if not item["pending"] or current_hash(path) == item["before"]:
                continue
            if current_hash(path) != item["new"]:
                raise DeployError("Rollback: файл изменился: " + name)
            if item["before"] is None:
                path.unlink()
                fsync_dir(path.parent)
            else:
                data = read_regular(backup / item["backup_file"])[0]
                atomic_write(path, data, item["new"], item["metadata"])
        for rel in sorted(manifest["created_directories"], key=lambda p: (-p.count("/"), p)):
            folder = safe_absolute(cases_root / rel, may_be_missing=True)
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
                fsync_dir(folder.parent)
        for name, item in manifest["entries"].items():
            if current_hash(target_path(cases_root, name)) != item["before"]:
                raise DeployError("Rollback verification failed: " + name)
        assert_neighbors(web_root, neighbors)
        manifest["status"] = "rolled_back"
        save_json(backup / "manifest.json", manifest)
        log(f"ROLLBACK=PASS; FILES_VERIFIED={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
        return {"status": "rolled_back", "backup": str(backup)}
    finally:
        if root_fd is not None:
            os.close(root_fd)


def deploy(archive_path, web_root, backups, *, apply=False, fetcher=fetch_public, fault=None, log=print):
    """Local root/backups/fetcher injection is for tests, not exposed as CLI flags."""
    bundle = load_bundle(archive_path)
    web_root = safe_absolute(Path(web_root))
    backups = Path(backups)
    if backups == web_root or web_root in backups.parents:
        raise DeployError("Backup должен находиться вне web-root")
    cases_root = safe_absolute(web_root / "uslugi/functional_dentistry")
    if not web_root.is_dir() or not cases_root.is_dir():
        raise DeployError("Отсутствует опубликованный каталог кейсов")
    root_fd = os.open(web_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        states = inspect_targets(cases_root, bundle)
        neighbors = snapshot_neighbors(web_root)
        verify_https({n: states[n]["sha256"] for n in HTML_PATHS}, fetcher)
        # HTTPS may take time: repeat all local guards before even creating backups.
        if any(current_hash(target_path(cases_root, n)) != s["sha256"] for n, s in states.items()):
            raise DeployError("Файлы изменились во время HTTPS preflight")
        assert_neighbors(web_root, neighbors)
        pending = sorted((n for n, s in states.items() if s["pending"]), key=lambda n: (n in HTML_PATHS, n))
        log("ROOT=" + str(web_root))
        log("ARCHIVE_SHA256=" + ARCHIVE_SHA256)
        log(f"PREFLIGHT=PASS; HTTPS_PAGES={len(HTML_PATHS)}; ALLOWED_FILES={len(ALLOWED_PATHS)}; PENDING=" + str(len(pending)))
        if not apply:
            return {"status": "check_only", "pending": len(pending), "web_root": str(web_root)}
        if not pending:
            verify_https({n: bundle.hashes[n]["new"] for n in ALLOWED_PATHS}, fetcher)
            assert_neighbors(web_root, neighbors)
            log(f"PORTRAITS=ALREADY_INSTALLED; FILES_VERIFIED={len(ALLOWED_PATHS)}; HTTPS_FILES={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
            return {"status": "already_installed", "pending": 0, "web_root": str(web_root)}
        backup, manifest = make_backup(web_root, backups, bundle, states, neighbors)
        log("BACKUP=" + str(backup))
        log("ROLLBACK_COMMAND=python3 " + shlex.quote(str(backup / "rollback.py")))
        assert_neighbors(web_root, neighbors)
        for name, state in states.items():
            if current_hash(target_path(cases_root, name)) != state["sha256"]:
                raise DeployError("Файл изменился при резервном копировании: " + name)
        owner = cases_root.stat()
        new_metadata = {"mode": 0o644, "uid": owner.st_uid, "gid": owner.st_gid}
        manifest["status"] = "applying"
        save_json(backup / "manifest.json", manifest)
        try:
            for name in pending:
                if fault:
                    fault("before_write", name)
                atomic_write(target_path(cases_root, name), bundle.files[name], states[name]["sha256"],
                             states[name]["metadata"] or new_metadata)
                if fault:
                    fault("after_write", name)
            for name in ALLOWED_PATHS:
                if current_hash(target_path(cases_root, name)) != bundle.hashes[name]["new"]:
                    raise DeployError("Локальный postcheck не пройден: " + name)
            assert_neighbors(web_root, neighbors)
            verify_https({n: bundle.hashes[n]["new"] for n in ALLOWED_PATHS}, fetcher)
            assert_neighbors(web_root, neighbors)
            manifest["status"] = "applied"
            save_json(backup / "manifest.json", manifest)
        except BaseException as error:
            try:
                rollback_backup(backup, locked=True, log=log)
                assert_neighbors(web_root, neighbors)
            except BaseException as rollback_error:
                log("ROLLBACK=BLOCKED; BACKUP=" + str(backup))
                raise DeployError("Ошибка установки: " + str(error) + "; rollback: " + str(rollback_error)) from error
            raise DeployError(f"Установка отменена, исходные {len(ALLOWED_PATHS)} пути восстановлены: " + str(error)) from error
        log(f"PORTRAITS=PASS; SPECIALISTS=8; FILES_VERIFIED={len(ALLOWED_PATHS)}; HTTPS_FILES={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
        return {"status": "applied", "pending": len(pending), "backup": str(backup), "web_root": str(web_root)}
    finally:
        os.close(root_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        raise DeployError("Для установки требуется root")
    deploy(None, discover_root(), Path("/var/backups/azmednsk"), apply=args.apply, fetcher=fetch_live)


if __name__ == "__main__":
    try:
        if Path(__file__).name == "rollback.py":
            rollback_backup(Path(__file__).resolve().parent)
        else:
            main()
    except (Exception, KeyboardInterrupt) as error:
        sys.exit("STOP=" + str(error))
