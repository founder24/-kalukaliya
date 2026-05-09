"""Tests for `supabase_jwks` — cache freshness, stale-grace, kid resolution.

These are hermetic — no real Supabase calls. The HTTP fetcher is
monkey-patched so we can exercise:
  * Fresh cache (no refresh).
  * Stale-grace (refresh attempted, fails, cache served).
  * Past-grace cold-fail (raises).
  * Single-key JWKS resolves a token with no kid header.
  * Token with bad signature → SupabaseTokenInvalid.
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_JWT_AUD", "authenticated")

import supabase_jwks  # noqa: E402


def _b64url_uint(n: int) -> str:
    import base64
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_keypair_and_jwks(kid: str = "kid-test"):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = priv.public_key().public_numbers()

    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    jwk = {
        "kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid,
        "n": _b64url_uint(nums.n), "e": _b64url_uint(nums.e),
    }
    return pem, {"keys": [jwk]}


def _mint_token(pem: str, *, kid: str | None, aud: str = "authenticated", exp_offset: int = 300, sub: str = "user-1"):
    import jwt as _pyjwt
    headers = {}
    if kid is not None:
        headers["kid"] = kid
    return _pyjwt.encode(
        {"sub": sub, "aud": aud, "exp": int(time.time()) + exp_offset, "iat": int(time.time())},
        pem,
        algorithm="RS256",
        headers=headers,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    supabase_jwks._reset_for_test()
    yield
    supabase_jwks._reset_for_test()


def test_fresh_cache_avoids_network(monkeypatch):
    pem, jwks = _make_keypair_and_jwks()
    calls = {"n": 0}

    def fake_fetch(url):
        calls["n"] += 1
        return jwks

    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", fake_fetch)
    token = _mint_token(pem, kid="kid-test")

    supabase_jwks.verify_supabase_jwt(token)
    supabase_jwks.verify_supabase_jwt(token)
    supabase_jwks.verify_supabase_jwt(token)

    assert calls["n"] == 1, "fresh cache must not re-fetch"


def test_stale_grace_serves_old_cache_on_fetch_failure(monkeypatch):
    pem, jwks = _make_keypair_and_jwks()
    seq = {"i": 0}

    def fake_fetch(url):
        seq["i"] += 1
        if seq["i"] == 1:
            return jwks
        raise RuntimeError("supabase down")

    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", fake_fetch)
    token = _mint_token(pem, kid="kid-test")

    supabase_jwks.verify_supabase_jwt(token)  # populates cache
    # Force the cache age past the fresh window but inside the grace.
    supabase_jwks._cache.fetched_at = time.time() - (supabase_jwks._JWKS_TTL_SECONDS + 30)

    claims = supabase_jwks.verify_supabase_jwt(token)
    assert claims["sub"] == "user-1"
    snap = supabase_jwks.cache_snapshot()
    assert snap["last_refresh_error"] is not None


def test_past_grace_with_failed_fetch_raises(monkeypatch):
    pem, jwks = _make_keypair_and_jwks()

    def fake_fetch(url):
        raise RuntimeError("supabase still down")

    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", fake_fetch)
    # Pretend cache exists but is fully expired (past TTL+grace).
    supabase_jwks._cache = supabase_jwks._JWKSCacheEntry(
        keys_by_kid={"kid-test": jwks["keys"][0]},
        fetched_at=time.time() - (supabase_jwks._JWKS_TTL_SECONDS + supabase_jwks._JWKS_STALE_GRACE_SECONDS + 5),
    )
    token = _mint_token(pem, kid="kid-test")

    with pytest.raises(supabase_jwks.SupabaseJWKSError):
        supabase_jwks.verify_supabase_jwt(token)


def test_single_key_jwks_resolves_token_without_kid(monkeypatch):
    pem, jwks = _make_keypair_and_jwks(kid="only-key")
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks)
    token = _mint_token(pem, kid=None)
    claims = supabase_jwks.verify_supabase_jwt(token)
    assert claims["sub"] == "user-1"


def test_bad_signature_rejected(monkeypatch):
    _, jwks_a = _make_keypair_and_jwks(kid="kid-a")
    pem_b, _ = _make_keypair_and_jwks(kid="kid-a")  # different key, same kid
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks_a)
    token = _mint_token(pem_b, kid="kid-a")
    with pytest.raises(supabase_jwks.SupabaseTokenInvalid):
        supabase_jwks.verify_supabase_jwt(token)


def test_expired_token_rejected(monkeypatch):
    pem, jwks = _make_keypair_and_jwks()
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks)
    token = _mint_token(pem, kid="kid-test", exp_offset=-30)
    with pytest.raises(supabase_jwks.SupabaseTokenInvalid):
        supabase_jwks.verify_supabase_jwt(token)


def test_unknown_kid_rejected(monkeypatch):
    pem, jwks = _make_keypair_and_jwks(kid="kid-a")
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks)
    token = _mint_token(pem, kid="kid-other")
    with pytest.raises(supabase_jwks.SupabaseTokenInvalid):
        supabase_jwks.verify_supabase_jwt(token)


def test_non_rs256_header_alg_rejected(monkeypatch):
    """Hard-pin: a token whose header advertises any alg other than RS256
    must be rejected up-front, regardless of what the JWKS entry says.
    Guards against the classic JWT alg-confusion footgun where an attacker
    flips alg to HS256 and tries to coerce the verifier into using the
    public key as an HMAC secret.
    """
    import jwt as _pyjwt
    pem, jwks = _make_keypair_and_jwks(kid="kid-test")
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks)
    # Mint a perfectly-valid RS512 token signed with the same RSA key —
    # signature math succeeds, but our verifier must still refuse.
    rs512_token = _pyjwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 300},
        pem,
        algorithm="RS512",
        headers={"kid": "kid-test"},
    )
    with pytest.raises(supabase_jwks.SupabaseTokenInvalid, match="unsupported alg"):
        supabase_jwks.verify_supabase_jwt(rs512_token)


def test_non_rs256_jwk_alg_rejected(monkeypatch):
    """Hard-pin: even if the token header is silent on alg, a JWKS entry
    that advertises a non-RS256 alg must be rejected. Belt-and-suspenders
    so a misconfigured JWKS upstream cannot widen the trust surface.
    """
    pem, jwks = _make_keypair_and_jwks(kid="kid-test")
    jwks["keys"][0]["alg"] = "RS384"
    monkeypatch.setattr(supabase_jwks, "_http_fetch_jwks", lambda url: jwks)
    token = _mint_token(pem, kid="kid-test")
    with pytest.raises(supabase_jwks.SupabaseTokenInvalid, match="unsupported alg"):
        supabase_jwks.verify_supabase_jwt(token)
