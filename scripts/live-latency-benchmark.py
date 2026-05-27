#!/usr/bin/env python3
"""
Live Latency Benchmark for syrabit.ai Production Deployment
============================================================

Measures real response times against the live syrabit.ai API endpoints.
Produces a formatted report with min/avg/max/P95 latency metrics and
pass/fail status based on configurable thresholds.

Usage:
    python3 scripts/live-latency-benchmark.py
    python3 scripts/live-latency-benchmark.py --json
    python3 scripts/live-latency-benchmark.py --help

Configuration (environment variables or .env file):
    API_BASE_URL    - Base URL for the API (default: https://api.syrabit.ai)
    TEST_EMAIL      - Email for authenticated endpoint testing
    TEST_PASSWORD   - Password for authenticated endpoint testing
    NUM_ITERATIONS  - Number of iterations per test (default: 5)
    CONCURRENCY     - Concurrent requests for load testing (default: 1)

Exit codes:
    0 - All endpoints within latency thresholds
    1 - One or more endpoints exceeded their threshold
"""

import argparse
import json
import os
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Latency thresholds in milliseconds
THRESHOLDS = {
    "edge_health": 100,       # Edge health should respond in <100ms
    "backend_health": 500,    # Backend health can take up to 500ms
    "deep_health": 500,       # Deep health check up to 500ms
    "auth_latency": 500,      # Auth endpoint up to 500ms
    "chat_latency": 3000,     # Chat/RAG pipeline up to 3000ms
}


def load_env_file(path: str = ".env") -> None:
    """
    Load environment variables from a .env file.
    Tries python-dotenv first; falls back to manual parsing.
    """
    # Try python-dotenv if available
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path)
        return
    except ImportError:
        pass

    # Manual fallback: parse KEY=VALUE lines
    if not os.path.isfile(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Remove surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Only set if not already in environment (env vars take precedence)
            if key not in os.environ:
                os.environ[key] = value


def get_config() -> Dict[str, Any]:
    """Read configuration from environment variables."""
    return {
        "api_base_url": os.environ.get("API_BASE_URL", "https://api.syrabit.ai").rstrip("/"),
        "test_email": os.environ.get("TEST_EMAIL", ""),
        "test_password": os.environ.get("TEST_PASSWORD", ""),
        "num_iterations": int(os.environ.get("NUM_ITERATIONS", "5")),
        "concurrency": int(os.environ.get("CONCURRENCY", "1")),
    }


# ---------------------------------------------------------------------------
# HTTP Utilities
# ---------------------------------------------------------------------------

def make_request(
    url: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> Tuple[int, float, str]:
    """
    Make an HTTP request and return (status_code, latency_ms, response_body).

    Uses urllib.request from stdlib - no external dependencies needed.
    """
    req_headers = {"User-Agent": "syrabit-latency-benchmark/1.0"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

    # Create SSL context that validates certificates
    ctx = ssl.create_default_context()

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        status = e.code
    except urllib.error.URLError as e:
        # Network/DNS/TLS failure
        elapsed = (time.perf_counter() - start) * 1000
        return 0, elapsed, f"URLError: {e.reason}"
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return 0, elapsed, f"Error: {e}"

    elapsed = (time.perf_counter() - start) * 1000
    return status, elapsed, body


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkResult:
    """Stores results for a single endpoint benchmark."""

    def __init__(self, name: str, threshold_ms: float):
        self.name = name
        self.threshold_ms = threshold_ms
        self.latencies: List[float] = []
        self.status_codes: List[int] = []
        self.errors: List[str] = []

    def add_sample(self, status: int, latency_ms: float, error: str = "") -> None:
        self.latencies.append(latency_ms)
        self.status_codes.append(status)
        if error:
            self.errors.append(error)

    @property
    def min_ms(self) -> float:
        return min(self.latencies) if self.latencies else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0.0

    @property
    def p95_ms(self) -> float:
        if len(self.latencies) < 2:
            return self.max_ms
        # Use sorted-based P95 for small sample sizes
        sorted_vals = sorted(self.latencies)
        idx = int(len(sorted_vals) * 0.95)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @property
    def passed(self) -> bool:
        """Pass if P95 latency is within threshold."""
        return self.p95_ms <= self.threshold_ms

    @property
    def dominant_status(self) -> str:
        """Most common status code as a string."""
        if not self.status_codes:
            return "N/A"
        # Filter out 0 (connection errors) for display
        codes = [c for c in self.status_codes if c != 0]
        if not codes:
            return "ERR"
        # Return most common
        return str(max(set(codes), key=codes.count))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "threshold_ms": self.threshold_ms,
            "min_ms": round(self.min_ms, 2),
            "avg_ms": round(self.avg_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "status_code": self.dominant_status,
            "passed": self.passed,
            "samples": len(self.latencies),
            "errors": self.errors[:5],  # Limit error output
        }


def run_single_test(
    url: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, float, str]:
    """Run a single request for benchmarking."""
    return make_request(url, method=method, data=data, headers=headers)


def run_benchmark(
    name: str,
    url: str,
    threshold_ms: float,
    num_iterations: int,
    concurrency: int,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> BenchmarkResult:
    """
    Run a benchmark for a given endpoint.

    Executes num_iterations requests with the specified concurrency level.
    """
    result = BenchmarkResult(name, threshold_ms)

    if concurrency <= 1:
        # Sequential execution
        for _ in range(num_iterations):
            status, latency, body = run_single_test(url, method, data, headers)
            error = body if status == 0 else ""
            result.add_sample(status, latency, error)
    else:
        # Concurrent execution using thread pool
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            for _ in range(num_iterations):
                future = executor.submit(run_single_test, url, method, data, headers)
                futures.append(future)

            for future in as_completed(futures):
                status, latency, body = future.result()
                error = body if status == 0 else ""
                result.add_sample(status, latency, error)

    return result


# ---------------------------------------------------------------------------
# Authentication Helper
# ---------------------------------------------------------------------------

def get_auth_token(base_url: str, email: str, password: str) -> Optional[str]:
    """
    Attempt to login and retrieve an auth token.
    Returns None if credentials are not provided or login fails.
    """
    if not email or not password:
        return None

    login_url = f"{base_url}/api/v1/auth/login"
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    status, _, body = make_request(login_url, method="POST", data=payload, headers=headers)

    if status == 200:
        try:
            data = json.loads(body)
            # Try common token field names
            return data.get("access_token") or data.get("token") or data.get("jwt")
        except (json.JSONDecodeError, AttributeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Main Benchmark Suite
# ---------------------------------------------------------------------------

def run_all_benchmarks(config: Dict[str, Any]) -> List[BenchmarkResult]:
    """Run all benchmark tests and return results."""
    base_url = config["api_base_url"]
    iterations = config["num_iterations"]
    concurrency = config["concurrency"]
    results: List[BenchmarkResult] = []

    print(f"\n{'=' * 70}")
    print(f"  Live Latency Benchmark - {base_url}")
    print(f"  Iterations: {iterations} | Concurrency: {concurrency}")
    print(f"{'=' * 70}\n")

    # --- Test 1: Edge Health ---
    print("  [1/5] Testing edge health endpoint...", end=" ", flush=True)
    r = run_benchmark(
        name="Edge Health (GET /health)",
        url=f"{base_url}/health",
        threshold_ms=THRESHOLDS["edge_health"],
        num_iterations=iterations,
        concurrency=concurrency,
    )
    results.append(r)
    print(f"done ({r.avg_ms:.0f}ms avg)")

    # --- Test 2: Backend Health ---
    print("  [2/5] Testing backend health endpoint...", end=" ", flush=True)
    r = run_benchmark(
        name="Backend Health (GET /api/v1/health)",
        url=f"{base_url}/api/v1/health",
        threshold_ms=THRESHOLDS["backend_health"],
        num_iterations=iterations,
        concurrency=concurrency,
    )
    results.append(r)
    print(f"done ({r.avg_ms:.0f}ms avg)")

    # --- Test 3: Deep Health ---
    print("  [3/5] Testing deep health endpoint...", end=" ", flush=True)
    r = run_benchmark(
        name="Deep Health (GET /api/v1/health/deep)",
        url=f"{base_url}/api/v1/health/deep",
        threshold_ms=THRESHOLDS["deep_health"],
        num_iterations=iterations,
        concurrency=concurrency,
    )
    results.append(r)
    print(f"done ({r.avg_ms:.0f}ms avg)")

    # --- Test 4: Auth Latency (invalid credentials) ---
    print("  [4/5] Testing auth endpoint (invalid creds)...", end=" ", flush=True)
    invalid_payload = json.dumps({
        "email": "benchmark-test@invalid.example.com",
        "password": "invalid-password-for-latency-test"
    }).encode("utf-8")
    r = run_benchmark(
        name="Auth Login (POST /api/v1/auth/login)",
        url=f"{base_url}/api/v1/auth/login",
        threshold_ms=THRESHOLDS["auth_latency"],
        num_iterations=iterations,
        concurrency=concurrency,
        method="POST",
        data=invalid_payload,
        headers={"Content-Type": "application/json"},
    )
    results.append(r)
    print(f"done ({r.avg_ms:.0f}ms avg)")

    # --- Test 5: Chat/RAG Latency (requires auth) ---
    print("  [5/5] Testing chat endpoint...", end=" ", flush=True)
    token = get_auth_token(base_url, config["test_email"], config["test_password"])
    if token:
        chat_payload = json.dumps({
            "message": "What is syrabit?",
            "session_id": "benchmark-test-session",
        }).encode("utf-8")
        chat_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        r = run_benchmark(
            name="Chat/RAG (POST /api/v1/chat)",
            url=f"{base_url}/api/v1/chat",
            threshold_ms=THRESHOLDS["chat_latency"],
            num_iterations=iterations,
            concurrency=concurrency,
            method="POST",
            data=chat_payload,
            headers=chat_headers,
        )
    else:
        # No valid credentials - test unauthenticated to measure auth rejection latency
        chat_payload = json.dumps({
            "message": "What is syrabit?",
            "session_id": "benchmark-test-session",
        }).encode("utf-8")
        r = run_benchmark(
            name="Chat/RAG (POST /api/v1/chat) [no auth]",
            url=f"{base_url}/api/v1/chat",
            threshold_ms=THRESHOLDS["chat_latency"],
            num_iterations=iterations,
            concurrency=concurrency,
            method="POST",
            data=chat_payload,
            headers={"Content-Type": "application/json"},
        )
    results.append(r)
    print(f"done ({r.avg_ms:.0f}ms avg)")

    return results


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

def print_table(results: List[BenchmarkResult]) -> None:
    """Print a formatted table of benchmark results."""
    # Column widths
    name_w = max(len(r.name) for r in results) + 2
    num_w = 10

    # Header
    print(f"\n{'=' * (name_w + num_w * 5 + 20)}")
    print(
        f"  {'Endpoint':<{name_w}}"
        f"{'Min(ms)':>{num_w}}"
        f"{'Avg(ms)':>{num_w}}"
        f"{'Max(ms)':>{num_w}}"
        f"{'P95(ms)':>{num_w}}"
        f"{'Status':>{num_w}}"
        f"  Result"
    )
    print(f"  {'-' * (name_w + num_w * 5 + 10)}")

    # Rows
    for r in results:
        status_icon = "PASS" if r.passed else "FAIL"
        threshold_note = f"(threshold: {r.threshold_ms:.0f}ms)"
        print(
            f"  {r.name:<{name_w}}"
            f"{r.min_ms:>{num_w}.1f}"
            f"{r.avg_ms:>{num_w}.1f}"
            f"{r.max_ms:>{num_w}.1f}"
            f"{r.p95_ms:>{num_w}.1f}"
            f"{r.dominant_status:>{num_w}}"
            f"  {status_icon} {threshold_note}"
        )

    # Footer
    print(f"  {'-' * (name_w + num_w * 5 + 10)}")
    passed_count = sum(1 for r in results if r.passed)
    total = len(results)
    overall = "ALL PASS" if passed_count == total else f"FAILED ({total - passed_count}/{total} exceeded threshold)"
    print(f"\n  Overall: {overall}\n")


def print_json(results: List[BenchmarkResult]) -> None:
    """Print results as JSON for machine-readable output."""
    output = {
        "benchmark": "live-latency",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [r.to_dict() for r in results],
        "all_passed": all(r.passed for r in results),
    }
    print(json.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure live response times against the syrabit.ai production deployment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration via environment variables or .env file:
  API_BASE_URL    Base URL (default: https://api.syrabit.ai)
  TEST_EMAIL      Email for authenticated endpoints
  TEST_PASSWORD   Password for authenticated endpoints
  NUM_ITERATIONS  Iterations per test (default: 5)
  CONCURRENCY     Concurrent requests (default: 1)

Examples:
  python3 scripts/live-latency-benchmark.py
  python3 scripts/live-latency-benchmark.py --json
  NUM_ITERATIONS=10 CONCURRENCY=3 python3 scripts/live-latency-benchmark.py
  API_BASE_URL=https://staging.syrabit.ai python3 scripts/live-latency-benchmark.py
""",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (machine-readable)",
    )
    return parser.parse_args()


def main() -> int:
    """
    Main entry point. Returns exit code:
    0 if all endpoints are within threshold, 1 otherwise.
    """
    args = parse_args()

    # Load .env file (supports python-dotenv or manual parsing)
    load_env_file()

    # Read configuration
    config = get_config()

    # Validate configuration
    if config["num_iterations"] < 1:
        print("ERROR: NUM_ITERATIONS must be at least 1", file=sys.stderr)
        return 1
    if config["concurrency"] < 1:
        print("ERROR: CONCURRENCY must be at least 1", file=sys.stderr)
        return 1

    # Run benchmarks
    if not args.json:
        results = run_all_benchmarks(config)
        print_table(results)
    else:
        # Suppress progress output for JSON mode
        # Redirect stdout temporarily for progress messages
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        results = run_all_benchmarks(config)
        sys.stdout = old_stdout
        print_json(results)

    # Exit with code 1 if any endpoint failed its threshold
    if not all(r.passed for r in results):
        if not args.json:
            print("  Exit code 1: one or more endpoints exceeded latency threshold.\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
