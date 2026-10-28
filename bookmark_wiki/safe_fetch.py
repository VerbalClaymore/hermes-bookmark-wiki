"""Bounded pinned-HTTPS acquisition with injected deterministic seams."""

from __future__ import annotations

import ipaddress
import json
import queue
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urljoin, urlsplit

from .context_state import BudgetExceeded, ContextStore, Locked


_MAX_TOTAL_TIMEOUT = 30.0
_MAX_BYTES = {"text": 2 * 1024 * 1024, "json": 2 * 1024 * 1024, "image": 16 * 1024 * 1024, "video": 64 * 1024 * 1024}
_ACCEPTS = {"default", "github_raw", "github_json"}


class FetchInputError(ValueError):
    pass


@dataclass
class FetchResult:
    ok: bool
    state: str
    body: bytes = b""
    final_url: str | None = None
    error_code: str | None = None
    reason: str = ""
    http_status: int | None = None
    redirects: int = 0
    attempts: list[str] = field(default_factory=list)


def _failed(code: str, reason: str, *, url: str | None = None, status: int | None = None, redirects: int = 0, attempts=None) -> FetchResult:
    return FetchResult(False, "failed", final_url=url, error_code=code, reason=reason, http_status=status, redirects=redirects, attempts=attempts or [])


def _globally_routable(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # is_global alone is not sufficient for multicast on all supported Python versions.
    return bool(address.is_global and not (address.is_private or address.is_link_local or address.is_loopback or address.is_reserved or address.is_unspecified or address.is_multicast))


def _numeric_disguised(hostname: str) -> bool:
    lowered = hostname.casefold()
    if re_fullmatch_digits_dots(lowered) or lowered.startswith("0x") and all(char in "0123456789abcdefx" for char in lowered):
        return True
    # Reject dotted decimal-looking names that ipaddress intentionally rejects as malformed.
    return all(part.isdigit() for part in lowered.split(".")) if "." in lowered else False


def re_fullmatch_digits_dots(value: str) -> bool:
    return bool(value) and all(char.isdigit() or char == "." for char in value)


def validate_https_url(url: str) -> object:
    """Validate strict HTTPS URL policy before DNS or connection."""
    if not isinstance(url, str) or len(url) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise FetchInputError("rejected_url")
    # Backslashes and percent escapes have multiple URL interpretations across parsers.
    if "\\" in url or "%" in url:
        raise FetchInputError("rejected_url")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FetchInputError("rejected_url") from exc
    if parsed.scheme.lower() != "https" or not parsed.netloc or not hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or port not in (None, 443):
        raise FetchInputError("rejected_url")
    if any(char.isspace() for char in hostname) or not hostname.isascii() or hostname.endswith("."):
        raise FetchInputError("rejected_url")
    lowered = hostname.casefold()
    if lowered == "localhost" or lowered.endswith(".localhost") or lowered.endswith(".local"):
        raise FetchInputError("rejected_url")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _globally_routable(hostname):
            raise FetchInputError("rejected_url")
    elif _numeric_disguised(lowered):
        raise FetchInputError("rejected_url")
    return parsed


def _default_resolver(host: str, port: int, deadline: float) -> list[str]:
    """Resolve in a daemon helper so getaddrinfo cannot block the acquisition caller."""
    result: queue.Queue = queue.Queue(maxsize=1)

    def worker():
        try:
            result.put([item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)])
        except BaseException as exc:
            result.put(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(max(0.0, deadline - time.monotonic()))
    if thread.is_alive():
        raise TimeoutError("dns deadline")
    value = result.get_nowait()
    if isinstance(value, BaseException):
        raise value
    return value


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(ip, port=443, timeout=timeout, context=ssl.create_default_context())
        self.original_host = host
        self.pinned_ip = ip

    def connect(self):
        deadline = time.monotonic() + self.timeout
        address = ipaddress.ip_address(self.pinned_ip)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            if family == socket.AF_INET6:
                sock.connect((self.pinned_ip, 443, 0, 0))
            else:
                sock.connect((self.pinned_ip, 443))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("TCP exhausted acquisition deadline")
            sock.settimeout(remaining)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.original_host)
        except BaseException:
            sock.close()
            raise


def _default_connection(url: str, ip: str, timeout: float):
    return _PinnedHTTPSConnection(urlsplit(url).hostname, ip, timeout)


def _header(headers, name: str, default: str = "") -> str:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower(), default)
        return value or default
    return default


def _accept_header(parsed, kind: str, accept: str) -> str:
    if accept not in _ACCEPTS:
        raise FetchInputError("invalid accept profile")
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if accept == "github_raw" or (accept == "default" and kind == "text" and host == "api.github.com" and path.endswith("/readme")):
        return "application/vnd.github.raw+json"
    if accept == "github_json" or (accept == "default" and kind == "json" and host == "api.github.com" and path.startswith("/repos/")):
        return "application/json"
    return "application/json" if kind == "json" else "*/*"


def _set_remaining_timeout(connection, response, remaining: float) -> None:
    if remaining <= 0:
        raise TimeoutError("request deadline elapsed")
    # Keep both http.client's future socket timeout and any already-created socket current.
    try:
        connection.timeout = remaining
    except (AttributeError, TypeError):
        pass
    sock = getattr(connection, "sock", None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(remaining)
    if response is not None:
        raw = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if raw is not None and hasattr(raw, "settimeout"):
            raw.settimeout(remaining)


def _content_ok(body: bytes, content_type: str, kind: str) -> bool:
    mime = content_type.lower().split(";", 1)[0].strip()
    if kind == "text":
        if not (mime.startswith("text/") or mime in {"application/json", "application/xml"} or mime.endswith("+json") or mime.endswith("+xml")):
            return False
        try:
            body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        return True
    if kind == "json":
        if not (mime == "application/json" or mime.endswith("+json") or mime == "text/json"):
            return False
        try:
            json.loads(body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return True
    if kind == "image":
        return mime in {"image/jpeg", "image/png"} and (body.startswith(b"\xff\xd8\xff") or body.startswith(b"\x89PNG\r\n\x1a\n"))
    if kind == "video":
        return mime == "video/mp4" and len(body) >= 12 and body[4:8] == b"ftyp"
    return False


def _close(resource) -> None:
    if resource is not None:
        try:
            resource.close()
        except Exception:
            pass


def fetch(
    url: str,
    *,
    store: ContextStore,
    kind: str = "text",
    max_bytes: int | None = None,
    total_timeout: float = 30.0,
    dns_resolver=None,
    connection_factory=None,
    clock=time.monotonic,
    accept: str = "default",
) -> FetchResult:
    """Acquire one public HTTPS object, reserving acquisition/HTTP budgets first."""
    if kind not in _MAX_BYTES:
        raise FetchInputError("invalid content kind")
    if isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float)) or total_timeout <= 0 or total_timeout > _MAX_TOTAL_TIMEOUT:
        raise FetchInputError("invalid total timeout")
    cap = _MAX_BYTES[kind] if max_bytes is None else max_bytes
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0 or cap > _MAX_BYTES[kind]:
        raise FetchInputError("invalid size cap")
    if accept not in _ACCEPTS:
        raise FetchInputError("invalid accept profile")
    try:
        with store.locked():
            if store.acquisition_stopped():
                return _failed("pilot_stopped", "acquisition stopped after a rate-limit response", url=url)
            try:
                # This reservation intentionally precedes URL parsing and DNS.
                store.reserve("acquisition", {"url": url, "kind": kind})
            except BudgetExceeded:
                return _failed("budget_exceeded", "acquisition budget exhausted", url=url)
            deadline = clock() + float(total_timeout)
            current = url
            redirects = 0
            attempts: list[str] = []
            while True:
                remaining = deadline - clock()
                if remaining <= 0:
                    return _failed("timeout", "total acquisition deadline elapsed", url=current, redirects=redirects, attempts=attempts)
                try:
                    parsed = validate_https_url(current)
                except FetchInputError:
                    return _failed("rejected_url", "URL failed HTTPS policy", url=current, redirects=redirects, attempts=attempts)
                resolver = dns_resolver or _default_resolver
                try:
                    addresses = list(resolver(parsed.hostname, 443, deadline))
                    if deadline - clock() <= 0:
                        raise TimeoutError("DNS deadline")
                    if not addresses or any(not isinstance(address, str) or not _globally_routable(address) for address in addresses):
                        return _failed("rejected_address", "DNS result is not globally routable", url=current, redirects=redirects, attempts=attempts)
                    ip = addresses[0]
                except TimeoutError:
                    return _failed("dns_timeout", "DNS deadline elapsed", url=current, redirects=redirects, attempts=attempts)
                except (OSError, ValueError, TypeError):
                    return _failed("dns_error", "DNS resolution failed", url=current, redirects=redirects, attempts=attempts)
                try:
                    store.reserve("http", {"url": current, "ip": ip})
                except BudgetExceeded:
                    return _failed("budget_exceeded", "HTTP request budget exhausted", url=current, redirects=redirects, attempts=attempts)
                connection = None
                response = None
                status = None
                try:
                    remaining = deadline - clock()
                    if remaining <= 0:
                        raise TimeoutError("request deadline elapsed")
                    connection = (connection_factory or _default_connection)(current, ip, remaining)
                    target = parsed.path or "/"
                    if parsed.query:
                        target += "?" + parsed.query
                    host_header = parsed.hostname
                    if ":" in host_header:
                        host_header = "[" + host_header + "]"
                    headers = {
                        "Host": host_header,
                        "User-Agent": "BookmarkWikiContextPilot/1",
                        "Accept": _accept_header(parsed, kind, accept),
                        "Accept-Encoding": "identity",
                    }
                    _set_remaining_timeout(connection, None, deadline - clock())
                    # Keyword-only headers is intentional: positional dict is an HTTP body.
                    connection.request("GET", target, headers=headers)
                    _set_remaining_timeout(connection, None, deadline - clock())
                    response = connection.getresponse()
                    _set_remaining_timeout(connection, response, deadline - clock())
                    try:
                        status = int(response.status)
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise HTTPException("invalid HTTP status") from exc
                    attempts.append(current)
                    content_type = _header(getattr(response, "headers", None), "Content-Type")
                    content_encoding = _header(getattr(response, "headers", None), "Content-Encoding", "identity")
                    if status in {301, 302, 303, 307, 308}:
                        location = _header(response.headers, "Location")
                        if not location or redirects >= 3:
                            return _failed("redirect_limit", "redirect limit or missing location", url=current, status=status, redirects=redirects, attempts=attempts)
                        current = urljoin(current, location)
                        redirects += 1
                        continue
                    if status in {401, 403, 404, 410}:
                        return FetchResult(False, "unavailable", final_url=current, error_code="unavailable", reason="remote access response", http_status=status, redirects=redirects, attempts=attempts)
                    if status != 200:
                        if status == 429:
                            store.stop_acquisition("HTTP 429 rate limit")
                            return _failed("rate_limit", "HTTP rate limit stopped pilot acquisition", url=current, status=status, redirects=redirects, attempts=attempts)
                        return _failed("http_status", "non-success HTTP response", url=current, status=status, redirects=redirects, attempts=attempts)
                    if content_encoding.lower() not in {"", "identity"}:
                        return _failed("compressed_content", "compressed response is rejected", url=current, status=status, redirects=redirects, attempts=attempts)
                    declared = _header(response.headers, "Content-Length")
                    if declared:
                        try:
                            if int(declared) < 0 or int(declared) > cap:
                                return _failed("size_limit", "content length exceeds cap", url=current, status=status, redirects=redirects, attempts=attempts)
                        except ValueError:
                            return _failed("bad_content", "invalid content length", url=current, status=status, redirects=redirects, attempts=attempts)
                    chunks: list[bytes] = []
                    total = 0
                    reader = getattr(response, "read1", None)
                    if not callable(reader):
                        reader = response.read
                    while True:
                        remaining = deadline - clock()
                        if remaining <= 0:
                            raise TimeoutError("read deadline elapsed")
                        _set_remaining_timeout(connection, response, remaining)
                        chunk = reader(min(65536, cap - total + 1))
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise HTTPException("invalid response bytes")
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > cap:
                            return _failed("size_limit", "stream exceeds cap", url=current, status=status, redirects=redirects, attempts=attempts)
                        chunks.append(bytes(chunk))
                    body = b"".join(chunks)
                    if not body or not _content_ok(body, content_type, kind):
                        return _failed("bad_content", "empty or unexpected content", url=current, status=status, redirects=redirects, attempts=attempts)
                    return FetchResult(True, "captured", body=body, final_url=current, http_status=200, redirects=redirects, attempts=attempts)
                except (socket.timeout, TimeoutError):
                    return _failed("timeout", "request deadline elapsed", url=current, status=status, redirects=redirects, attempts=attempts)
                except HTTPException:
                    return _failed("http_error", "HTTP protocol failed", url=current, status=status, redirects=redirects, attempts=attempts)
                except (OSError, ValueError, TypeError):
                    return _failed("network_error", "connection or response failed", url=current, status=status, redirects=redirects, attempts=attempts)
                finally:
                    _close(response)
                    _close(connection)
    except Locked:
        return _failed("locked", "shared update lock is held", url=url)
