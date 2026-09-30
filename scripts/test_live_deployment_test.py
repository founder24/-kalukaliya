#!/usr/bin/env python3
"""Local regression tests for live-deployment-test.sh; never targets production."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LIVE_TEST = ROOT / "scripts" / "live-deployment-test.sh"
RUN_ALL = ROOT / "scripts" / "run-all-live-tests.sh"
SAFE_CATEGORIES = ("health", "content", "seo", "edge", "security", "performance")
LARGE_HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'></head><body>"
    + b"x" * 400_000
    + b"</body></html>"
)


@dataclass
class RequestLog:
    root_location: str = "/library"
    requests: list[tuple[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, method: str, path: str) -> None:
        with self.lock:
            self.requests.append((method, path))

    def snapshot(self) -> list[tuple[str, str]]:
        with self.lock:
            return list(self.requests)


def make_handler(log: RequestLog) -> type[BaseHTTPRequestHandler]:
    class MockHandler(BaseHTTPRequestHandler):
        server_version = "mock-edge"
        sys_version = ""

        def log_message(self, *_args: object) -> None:
            pass

        def _record(self) -> None:
            log.record(self.command, self.path)

        def _respond(
            self,
            status: int,
            body: bytes = b"",
            content_type: str = "application/json",
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Cache-Control", "public, max-age=60")
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            self._record()
            path = urlsplit(self.path).path

            if path == "/":
                self._respond(301, extra_headers={"Location": log.root_location})
            elif path == "/library":
                self._respond(200, LARGE_HTML, "text/html; charset=utf-8")
            elif path == "/health":
                self._respond(200, b'{"status":"healthy","service":"mock"}')
            elif path == "/health/full":
                self._respond(
                    200,
                    b'{"status":"healthy","backend":{"status":"healthy"}}',
                )
            elif path == "/health/deep":
                self._respond(401, b'{"detail":"Deep health authorization required"}')
            elif path == "/health/circuit-breakers":
                self._respond(404, b'{"detail":"Not found"}')
            elif path == "/api/v1/content/library-bundle":
                headers = {}
                if "cache_bust_" not in self.path:
                    headers = {"X-Cache": "HIT", "CF-Cache-Status": "HIT"}
                self._respond(
                    200,
                    b'{"boards":[{}],"subjects":[{}],"chapters":[{}]}',
                    extra_headers=headers,
                )
            elif path.startswith("/api/v1/seo/sitemap"):
                self._respond(
                    200,
                    b'<?xml version="1.0"?><urlset></urlset>',
                    "application/xml",
                )
            elif path == "/robots.txt":
                self._respond(
                    200,
                    b"Sitemap: https://mock.invalid/sitemap.xml",
                    "text/plain",
                )
            elif path == "/manifest.json":
                self._respond(200, b'{"name":"mock"}')
            elif path.startswith("/api/v1/"):
                self._respond(404, b'{"detail":"Not found"}')
            else:
                self._respond(
                    200,
                    b"<!doctype html><html><head><meta charset='utf-8'></head><body>mock</body></html>",
                    "text/html; charset=utf-8",
                )

        def do_HEAD(self) -> None:
            self._record()
            self._respond(405)

        def do_POST(self) -> None:
            self._record()
            self._respond(405, b'{"detail":"Method not allowed"}')

        def do_PUT(self) -> None:
            self._record()
            self._respond(405, b'{"detail":"Method not allowed"}')

        def do_PATCH(self) -> None:
            self._record()
            self._respond(405, b'{"detail":"Method not allowed"}')

        def do_DELETE(self) -> None:
            self._record()
            self._respond(405, b'{"detail":"Method not allowed"}')

    return MockHandler


@contextmanager
def mock_server(root_location: str = "/library"):
    log = RequestLog(root_location=root_location)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield log, f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def local_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(name, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = env["NO_PROXY"]
    return env


def run_live_test(
    backend_url: str,
    frontend_url: str,
    category: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "bash",
        str(LIVE_TEST),
        "--backend-url",
        backend_url,
        "--frontend-url",
        frontend_url,
    ]
    if category:
        command.extend(["--category", category])
    return subprocess.run(
        command,
        cwd=ROOT,
        env=local_environment(),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


class LiveDeploymentRegressionTests(unittest.TestCase):
    def test_safe_default_checks_health_content_and_local_redirects(self) -> None:
        with mock_server() as (log, base_url):
            result = run_live_test(base_url, base_url)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Categories: " + ",".join(SAFE_CATEGORIES), output)
        self.assertIn("PASS /health/full reports healthy", output)
        self.assertIn("PASS /health/deep requires authorization (401)", output)
        self.assertIn(
            "WARN /health/circuit-breakers is optional and absent (404; informational)",
            output,
        )
        self.assertIn(
            "PASS frontend / reaches HTTP 200 after 1 same-origin redirect(s)",
            output,
        )
        self.assertIn("Cache invalidation: busted URL returned fresh content", output)
        self.assertIn("PASSED:", output)
        self.assertIn("FAILED: 0", output)

        requests = log.snapshot()
        self.assertTrue(requests)
        self.assertNotIn(("GET", "/health/full"), [])  # Keep request assertions explicit below.
        paths = [path for _method, path in requests]
        methods = {method for method, _path in requests}
        self.assertIn("/health/full", paths)
        self.assertIn("/health/deep", paths)
        self.assertLessEqual(methods, {"GET", "OPTIONS"})
        self.assertFalse(
            any(method in {"POST", "PUT", "PATCH", "DELETE"} for method, _ in requests),
            requests,
        )
        forbidden_paths = (
            "/api/v1/payments",
            "/api/webhooks/",
            "/api/v1/admin",
            "/api/v1/auth",
            "/api/v1/chat",
        )
        self.assertFalse(
            any(
                forbidden in path
                for _method, path in requests
                for forbidden in forbidden_paths
            ),
            requests,
        )
        self.assertIn(
            ("GET", "/api/v1/content/render/../../../etc/passwd"),
            requests,
        )

    def test_off_origin_redirect_is_rejected_before_target_request(self) -> None:
        with mock_server() as (target_log, target_url):
            with mock_server(root_location=f"{target_url}/library") as (
                source_log,
                source_url,
            ):
                result = run_live_test(source_url, source_url, category="edge")

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(
            "FAIL [CRITICAL] frontend / redirect points outside the configured origin",
            output,
        )
        self.assertEqual(target_log.snapshot(), [])
        self.assertIn(("GET", "/"), source_log.snapshot())

    def test_safe_category_list_is_used_by_script_and_master_runner(self) -> None:
        expected = ",".join(SAFE_CATEGORIES)
        help_result = subprocess.run(
            ["bash", str(LIVE_TEST), "--help"],
            cwd=ROOT,
            env=local_environment(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
        self.assertIn(f"Safe default: {expected}", help_result.stdout)

        script_source = LIVE_TEST.read_text()
        default_match = re.search(
            r'^DEFAULT_CATEGORIES="([^"]+)"$',
            script_source,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(default_match)
        self.assertEqual(default_match.group(1), expected)

        runner_source = RUN_ALL.read_text()
        deployment_call = runner_source.split('run_suite "deployment"', 1)[1].split(
            "# Suite 5",
            1,
        )[0]
        category_match = re.search(
            r'"--category"\s+"([^"]+)"',
            deployment_call,
        )
        self.assertIsNotNone(category_match)
        self.assertEqual(category_match.group(1), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)