"""Conservative Nginx config editor. Only append one include in the
explicitly selected TLS server block; preserve all other original bytes."""
from dataclasses import dataclass
import re

LOCATIONS = "/etc/nginx/az-ident006b-locations.conf"
PATHS = ("/api/ident-bridge/v1/batches", "/api/ident-bridge/v1/status")


class EditError(ValueError):
    pass


@dataclass
class Token:
    value: str
    start: int
    end: int
    kind: str = "word"


@dataclass
class Directive:
    words: list[str]
    children: list | None
    close: int | None = None


def tokens(text):
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            continue
        if text[i] in "{};":
            yield Token(text[i], i, i + 1, text[i])
            i += 1
            continue
        start = i
        parts = []
        quote = None
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                if i + 1 >= len(text):
                    raise EditError("INCOMPLETE_ESCAPE")
                parts.append(text[i:i+2])
                i += 2
                continue
            if quote:
                if ch == quote:
                    quote = None
                else:
                    parts.append(ch)
                i += 1
                continue
            if ch in "'\"":
                quote = ch
                i += 1
                continue
            # Braced variable references are arguments, not nested blocks.
            if text.startswith("$" + "{", i):
                end = text.find("}", i + 2)
                if end < 0 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text[i+2:end]):
                    raise EditError("UNSUPPORTED_VARIABLE")
                parts.append(text[i:end+1])
                i = end + 1
                continue
            if ch.isspace() or ch in "{};#":
                break
            parts.append(ch)
            i += 1
        if quote or i == start:
            raise EditError("UNSUPPORTED_NGINX_SYNTAX")
        yield Token("".join(parts), start, i)


def parse(text):
    ts = list(tokens(text))
    i = 0

    def block(nested=False):
        nonlocal i
        out = []
        words = []
        while i < len(ts):
            tok = ts[i]
            i += 1
            if tok.kind == "word":
                words.append(tok.value)
            elif tok.kind == ";":
                if not words:
                    raise EditError("EMPTY_DIRECTIVE")
                out.append(Directive(words, None))
                words = []
            elif tok.kind == "{":
                if not words:
                    raise EditError("ANONYMOUS_BLOCK")
                children, close = block(True)
                out.append(Directive(words, children, close))
                words = []
            elif tok.kind == "}":
                if not nested or words:
                    raise EditError("INVALID_BLOCK")
                return out, tok.start
        if nested or words:
            raise EditError("UNCLOSED_DIRECTIVE")
        return out, None

    return block()[0]


def valid_hostname(host):
    return (isinstance(host,str) and len(host) <= 253 and "." in host
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")))


def tls_443(words):
    if len(words) < 3 or "ssl" not in words[2:]:
        return False
    address = words[1]
    return address == "443" or bool(re.fullmatch(r"(?:[0-9.]+|\[[0-9A-Fa-f:]+\]|\*):443",address))


def plan(original: bytes, host: str) -> bytes:
    if not valid_hostname(host):
        raise EditError("HOSTNAME_REQUIRED")
    if len(original) > 1024*1024 or b"\x00" in original:
        raise EditError("INVALID_VHOST_BYTES")
    try:
        text = original.decode("utf-8")
    except UnicodeError as e:
        raise EditError("VHOST_UTF8_REQUIRED") from e
    servers = [n for n in parse(text) if n.words == ["server"] and n.children is not None]
    matching = []
    for n in servers:
        names = [c for c in n.children if c.words[0] == "server_name"]
        if any(host in c.words[1:] for c in names):
            if len(names) != 1:
                raise EditError("AMBIGUOUS_SERVER_NAME")
            listens = [c for c in n.children if c.words[0] == "listen"]
            if not listens:
                raise EditError("EXPLICIT_TLS_LISTEN_REQUIRED")
            if not any(tls_443(c.words) for c in listens):
                continue
            if not all(tls_443(c.words) for c in listens):
                raise EditError("MIXED_OR_UNSUPPORTED_LISTEN")
            matching.append(n)
    if len(matching) != 1:
        raise EditError("EXACTLY_ONE_TLS_VHOST_REQUIRED")
    server = matching[0]
    for c in server.children:
        name = c.words[0]
        if name in ("if","rewrite","return","error_page"):
            raise EditError("SERVER_LEVEL_REWRITE_OR_ERROR_PAGE")
        if name == "include" and LOCATIONS in c.words:
            raise EditError("RECEIVER_INCLUDE_ALREADY_PRESENT")
        if name == "location" and any(p in c.words[1:] for p in PATHS):
            raise EditError("RECEIVER_ROUTE_ALREADY_PRESENT")
    if server.close is None or LOCATIONS in text:
        raise EditError("RECEIVER_INCLUDE_ALREADY_PRESENT")
    newline = "\r\n" if "\r\n" in text else "\n"
    insert = newline + "    include " + LOCATIONS + ";" + newline
    return (text[:server.close]+insert+text[server.close:]).encode("utf-8")

def render_locations(template: bytes, host: str) -> bytes:
    if not valid_hostname(host) or template.count(b"__IDENT_HTTPS_HOST__") != 4:
        raise EditError("ROUTE_HOST_TEMPLATE")
    return template.replace(b"__IDENT_HTTPS_HOST__", host.encode("ascii"))
