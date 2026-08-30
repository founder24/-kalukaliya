from unittest.mock import MagicMock

from app.core.anon import resolve_anon_id


def _request(headers: dict[str, str], client_ip: str = "198.51.100.7") -> MagicMock:
    request = MagicMock()
    request.headers = headers
    request.client.host = client_ip
    return request


def test_browser_anonymous_id_wins_over_proxy_ip():
    """A returning browser must read the same quota despite a different proxy IP."""
    anon_id = "anon_0123456789abcdef0123456789abcdef"
    request = _request({"x-anon-id": anon_id, "X-Real-IP": "203.0.113.9"})

    assert resolve_anon_id(request) == anon_id


def test_invalid_browser_id_cannot_override_ip_fallback():
    request = _request({"x-anon-id": "not a valid quota key", "X-Real-IP": "203.0.113.9"})

    assert resolve_anon_id(request) == "ip_203_0_113_9"


def test_browser_identity_must_match_the_cross_stack_contract():
    request = _request(
        {"x-anon-id": "anon_0123456789abcdef0123456789abcdefx", "X-Real-IP": "203.0.113.9"}
    )

    assert resolve_anon_id(request) == "ip_203_0_113_9"