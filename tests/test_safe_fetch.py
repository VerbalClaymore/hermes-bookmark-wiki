import tempfile
import unittest
from pathlib import Path

from bookmark_wiki import context_state, safe_fetch


class Response:
    def __init__(self, status=200, body=b"synthetic body", headers=None):
        self.status = status
        self.reason = "synthetic"
        self.headers = headers or {"Content-Type": "text/plain", "Content-Length": str(len(body))}
        self._body = body
        self.closed = False

    def read(self, size=-1):
        if size < 0:
            value, self._body = self._body, b""
            return value
        value, self._body = self._body[:size], self._body[size:]
        return value

    def close(self):
        self.closed = True


class Connection:
    instances = []

    def __init__(self, url, ip, timeout, response):
        self.url, self.ip, self.timeout, self.response = url, ip, timeout, response
        self.requests = []
        Connection.instances.append(self)

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        pass


class SafeFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = context_state.ContextStore(Path(self.tmp.name))
        Connection.instances.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, url="https://public.example.test/file.txt", response=None, resolver=None, **kwargs):
        response = response or Response()
        resolver = resolver or (lambda host, port, deadline: ["93.184.216.34"])
        return safe_fetch.fetch(
            url,
            store=self.store,
            dns_resolver=resolver,
            connection_factory=lambda u, ip, timeout: Connection(u, ip, timeout, response),
            **kwargs,
        )

    def test_tcp_time_is_subtracted_before_tls_handshake(self):
        from unittest.mock import patch, MagicMock
        connection = safe_fetch._PinnedHTTPSConnection("public.example.test", "93.184.216.34", 30)
        sock = MagicMock()
        connection._context = MagicMock()
        with patch.object(safe_fetch.socket, "socket", return_value=sock), patch.object(safe_fetch.time, "monotonic", side_effect=[100.0, 121.0]):
            connection.connect()
        self.assertEqual(sock.settimeout.call_args_list[-1].args, (9.0,))
        connection._context.wrap_socket.assert_called_once_with(sock, server_hostname="public.example.test")

    def test_pinned_https_uses_validated_ip_original_host_and_fixed_headers(self):
        result = self.fetch()
        self.assertTrue(result.ok)
        self.assertEqual(result.body, b"synthetic body")
        self.assertEqual(result.http_status, 200)
        connection = Connection.instances[0]
        self.assertEqual(connection.ip, "93.184.216.34")
        self.assertEqual(connection.requests[0][2]["Host"], "public.example.test")
        self.assertEqual(connection.requests[0][2]["User-Agent"], "BookmarkWikiContextPilot/1")
        self.assertEqual(connection.requests[0][2]["Accept-Encoding"], "identity")

    def test_http_headers_are_keyword_argument_and_github_accept_profiles_are_fixed(self):
        class KeywordOnlyConnection(Connection):
            def request(self, method, target, *, headers):
                super().request(method, target, headers=headers)

        def run(url, kind, response):
            return safe_fetch.fetch(
                url, store=self.store, kind=kind,
                dns_resolver=lambda host, port, deadline: ["93.184.216.34"],
                connection_factory=lambda u, ip, timeout: KeywordOnlyConnection(u, ip, timeout, response),
            )

        readme = run("https://api.github.com/repos/example/project/readme", "text", Response(200, b"README", {"Content-Type": "text/plain"}))
        self.assertTrue(readme.ok)
        self.assertEqual(Connection.instances[-1].requests[0][2]["Accept"], "application/vnd.github.raw+json")
        metadata = run("https://api.github.com/repos/example/project", "json", Response(200, b"{}", {"Content-Type": "application/json"}))
        self.assertTrue(metadata.ok)
        self.assertEqual(Connection.instances[-1].requests[0][2]["Accept"], "application/json")

    def test_private_mixed_dns_redirect_userinfo_scheme_and_port_fail_before_connection(self):
        cases = [
            "http://public.example.test/a",
            "https://user:pass@public.example.test/a",
            "https://public.example.test:8443/a",
            "https://public.example.test/a#fragment",
            "https://127.0.0.1/a",
            "https://[::1]/a",
            "https://public.example.test/\n/a",
        ]
        for url in cases:
            with self.subTest(url=url):
                result = self.fetch(url)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "rejected_url")
                self.assertEqual(len(Connection.instances), 0)
                Connection.instances.clear()
        result = self.fetch(resolver=lambda host, port, deadline: ["93.184.216.34", "192.168.1.9"])
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "rejected_address")
        self.assertEqual(len(Connection.instances), 0)

        redirect_root = Path(self.tmp.name) / "redirect-local"
        redirect_root.mkdir()
        self.store = context_state.ContextStore(redirect_root)
        Connection.instances.clear()
        redirect = Response(302, b"", {"Location": "https://127.0.0.1/private"})
        result = safe_fetch.fetch(
            "https://public.example.test/start.txt", store=self.store,
            dns_resolver=lambda host, port, deadline: ["93.184.216.34"],
            connection_factory=lambda u, ip, timeout: Connection(u, ip, timeout, redirect),
        )
        self.assertEqual(result.error_code, "rejected_url")
        self.assertEqual(len(Connection.instances), 1)

    def test_total_timeout_size_caps_and_additional_url_content_hardening(self):
        timeout_root = Path(self.tmp.name) / "timeout-caps"
        timeout_root.mkdir()
        self.store = context_state.ContextStore(timeout_root)
        Connection.instances.clear()
        with self.assertRaises(safe_fetch.FetchInputError):
            safe_fetch.fetch("https://public.example.test/a", store=self.store, total_timeout=30.01, dns_resolver=lambda *args: ["93.184.216.34"])
        with self.assertRaises(safe_fetch.FetchInputError):
            safe_fetch.fetch("https://public.example.test/a", store=self.store, max_bytes=2 * 1024 * 1024 + 1, dns_resolver=lambda *args: ["93.184.216.34"])
        invalid_utf8 = self.fetch(response=Response(200, b"\xff", {"Content-Type": "text/plain"}))
        self.assertEqual(invalid_utf8.error_code, "bad_content")
        invalid_json = self.fetch(kind="json", response=Response(200, b"not json", {"Content-Type": "application/json"}))
        self.assertEqual(invalid_json.error_code, "bad_content")
        for index, url in enumerate(("https://localhost/a", "https://thing.localhost/a", "https://thing.local/a", "https://2130706433/a", "https://public.example.test/%2f/a", "https://public.example.test\\@example.test/a")):
            root = Path(self.tmp.name) / ("additional-url-" + str(index))
            root.mkdir()
            self.store = context_state.ContextStore(root)
            with self.subTest(url=url):
                Connection.instances.clear()
                result = self.fetch(url)
                self.assertEqual(result.error_code, "rejected_url")
                self.assertEqual(len(Connection.instances), 0)
                Connection.instances.clear()

    def test_redirects_are_bounded_and_statuses_are_honest(self):
        responses = [
            Response(302, b"", {"Location": "https://public.example.test/next"}),
            Response(200, b"final", {"Content-Type": "text/plain", "Content-Length": "5"}),
        ]
        result = safe_fetch.fetch(
            "https://public.example.test/start.txt",
            store=self.store,
            dns_resolver=lambda host, port, deadline: ["93.184.216.34"],
            connection_factory=lambda u, ip, timeout: Connection(u, ip, timeout, responses.pop(0)),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.redirects, 1)
        for status in (401, 403, 404, 410):
            status_root = Path(self.tmp.name) / ("unavailable-" + str(status))
            status_root.mkdir()
            self.store = context_state.ContextStore(status_root)
            result = self.fetch(response=Response(status, b"denied", {"Content-Type": "text/plain"}))
            self.assertEqual(result.state, "unavailable")
            self.assertEqual(result.http_status, status)
        for status in (204, 206, 304, 400, 429, 500):
            status_root = Path(self.tmp.name) / ("failed-" + str(status))
            status_root.mkdir()
            self.store = context_state.ContextStore(status_root)
            result = self.fetch(response=Response(status, b"bad", {"Content-Type": "text/plain"}))
            self.assertEqual(result.state, "failed")
            self.assertEqual(result.http_status, status)

    def test_failed_content_never_becomes_absence(self):
        for response in (
            Response(200, b"", {"Content-Type": "text/plain", "Content-Length": "0"}),
            Response(200, b"compressed", {"Content-Type": "text/plain", "Content-Encoding": "gzip"}),
            Response(200, b"html", {"Content-Type": "application/octet-stream"}),
            Response(200, b"too large", {"Content-Type": "text/plain", "Content-Length": "999"}),
        ):
            result = self.fetch(response=response, max_bytes=4)
            self.assertFalse(result.ok)
            self.assertEqual(result.state, "failed")
            self.assertNotEqual(result.state, "no_media_found_in_scope")

    def test_budget_is_reserved_before_dns_and_25th_http_is_rejected(self):
        observed = []

        def resolver(host, port, deadline):
            observed.append(len(list((Path(self.tmp.name) / "state" / "context-pilot" / "pilot-v1" / "attempts").glob("*.json"))))
            return ["93.184.216.34"]

        result = self.fetch(resolver=resolver)
        self.assertTrue(result.ok)
        self.assertGreaterEqual(observed[0], 1)
        with self.store.locked():
            for _ in range(23):
                self.store.reserve("http")
        result = self.fetch()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "budget_exceeded")
        self.assertEqual(len(Connection.instances), 1)

    def test_dns_timeout_redirect_loop_and_size_limit_are_structured(self):
        result = self.fetch(resolver=lambda host, port, deadline: (_ for _ in ()).throw(TimeoutError("deadline")))
        self.assertEqual(result.error_code, "dns_timeout")
        responses = [Response(302, b"", {"Location": "https://public.example.test/loop"}) for _ in range(5)]
        result = safe_fetch.fetch(
            "https://public.example.test/start.txt", store=self.store,
            dns_resolver=lambda host, port, deadline: ["93.184.216.34"],
            connection_factory=lambda u, ip, timeout: Connection(u, ip, timeout, responses.pop(0)),
        )
        self.assertEqual(result.error_code, "redirect_limit")

    def test_rate_limit_stops_later_acquisition_without_dns(self):
        result = self.fetch(response=Response(429, b"rate", {"Content-Type": "text/plain"}))
        self.assertEqual(result.error_code, "rate_limit")
        calls = []
        result = self.fetch(resolver=lambda *args: calls.append(args) or ["93.184.216.34"])
        self.assertEqual(result.error_code, "pilot_stopped")
        self.assertEqual(calls, [])

    def test_image_and_video_magic_are_checked(self):
        image = self.fetch(
            response=Response(200, b"not-jpeg", {"Content-Type": "image/jpeg", "Content-Length": "8"}),
            kind="image",
        )
        self.assertEqual(image.error_code, "bad_content")
        video = self.fetch(
            response=Response(200, b"not-mp4", {"Content-Type": "video/mp4", "Content-Length": "7"}),
            kind="video",
        )
        self.assertEqual(video.error_code, "bad_content")


if __name__ == "__main__":
    unittest.main()
