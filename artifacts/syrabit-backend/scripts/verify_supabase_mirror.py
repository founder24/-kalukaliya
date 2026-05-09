#!/usr/bin/env python3
"""Reconcile Mongo `db.users` against Supabase Auth.

Task #47 step 1 — Hard-block the cutover unless every active local
user has a matching Supabase Auth row. Run this BEFORE the
maintenance-window deploy:

    python scripts/verify_supabase_mirror.py             # report only
    python scripts/verify_supabase_mirror.py --fail-on-missing  # exit non-zero on drift

Output is JSON on stdout (machine-parseable for the runbook
checklist). Counts on stderr.

What is checked:
  * Every Mongo user with `status != 'banned'` and a non-empty
    `email` MUST appear in Supabase Auth (case-insensitive).
  * Mongo users with `auth_provider == 'google'` are reported
    separately — the OAuth broker auto-creates them on first
    sign-in, so a missing row there is acceptable PRE-cutover but
    means the user has not signed in via the Supabase OAuth flow
    yet (they will be silently locked out post-cutover).
  * Banned users are explicitly excluded; banning happens in
    Mongo and Supabase has no equivalent state.

Out of scope (would have its own task):
  * Reverse direction (Supabase rows that have no Mongo profile).
    These are harmless — `routes/auth.py:supabase_session`
    auto-creates the Mongo profile on first call.
  * Password-hash parity. Supabase owns the credential store
    after cutover; Mongo `password_hash` becomes dead data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("verify_supabase_mirror")


async def _fetch_mongo_users() -> list[dict]:
    """Pull every non-banned Mongo user with an email address."""
    from deps import db

    if db is None:
        raise RuntimeError("Mongo client not initialised — check MONGO_URL")
    cursor = db.users.find(
        {"status": {"$ne": "banned"}, "email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "email": 1, "auth_provider": 1, "status": 1, "is_admin": 1},
    )
    return [r async for r in cursor]


def _fetch_supabase_emails(supa_client) -> set[str]:
    emails: set[str] = set()
    page = 1
    per_page = 1000
    while True:
        try:
            result = supa_client.auth.admin.list_users(page=page, per_page=per_page)
            users = result if isinstance(result, list) else getattr(result, "users", result) or []
        except Exception as exc:
            logger.warning("list_users page %d failed: %s", page, exc)
            break
        if not users:
            break
        for u in users:
            email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            if email:
                emails.add(email.strip().lower())
        if len(users) < per_page:
            break
        page += 1
    return emails


def _bucket(local_users: Iterable[dict], supa_emails: set[str]) -> dict:
    matched: list[dict] = []
    missing_email_pwd: list[dict] = []   # auth_provider != google → HARD BLOCK
    missing_google_unseen: list[dict] = []  # auth_provider == google, not yet OAuth'd into Supabase
    for u in local_users:
        email = (u.get("email") or "").strip().lower()
        if not email:
            continue
        if email in supa_emails:
            matched.append(u)
            continue
        provider = (u.get("auth_provider") or "email").lower()
        if provider == "google":
            missing_google_unseen.append(u)
        else:
            missing_email_pwd.append(u)
    return {
        "matched": matched,
        "missing_email_password": missing_email_pwd,
        "missing_google_unseen": missing_google_unseen,
    }


async def run(fail_on_missing: bool, sample_limit: int) -> int:
    from deps import supa as _supa_admin

    if _supa_admin is None:
        raise RuntimeError(
            "Supabase admin client not configured — set SUPABASE_URL + SUPABASE_SERVICE_KEY"
        )

    logger.info("Pulling Mongo users…")
    local_users = await _fetch_mongo_users()
    logger.info("  %d active Mongo users with email", len(local_users))

    logger.info("Pulling Supabase Auth users…")
    supa_emails = await asyncio.to_thread(_fetch_supabase_emails, _supa_admin)
    logger.info("  %d Supabase Auth emails", len(supa_emails))

    buckets = _bucket(local_users, supa_emails)

    def _redact(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows[:sample_limit]:
            email = r.get("email", "")
            if email and "@" in email:
                local, _, dom = email.partition("@")
                redacted = (local[:2] + "***@" + dom) if len(local) > 2 else ("***@" + dom)
            else:
                redacted = email
            out.append({
                "id": r.get("id"),
                "email_redacted": redacted,
                "auth_provider": r.get("auth_provider"),
                "is_admin": bool(r.get("is_admin")),
            })
        return out

    report = {
        "task": "47",
        "step": "1-verify-supabase-mirror",
        "totals": {
            "mongo_active_users_with_email": len(local_users),
            "supabase_auth_emails": len(supa_emails),
            "matched": len(buckets["matched"]),
            "missing_email_password_HARD_BLOCK": len(buckets["missing_email_password"]),
            "missing_google_unseen_SOFT": len(buckets["missing_google_unseen"]),
        },
        "samples": {
            "missing_email_password_HARD_BLOCK": _redact(buckets["missing_email_password"]),
            "missing_google_unseen_SOFT": _redact(buckets["missing_google_unseen"]),
        },
        "cutover_safe": (len(buckets["missing_email_password"]) == 0),
    }

    print(json.dumps(report, indent=2, sort_keys=True))

    if not report["cutover_safe"]:
        logger.error(
            "HARD BLOCK: %d email/password users have no Supabase Auth row; "
            "run scripts/sync_users_to_supabase.py before the cutover",
            len(buckets["missing_email_password"]),
        )
        if fail_on_missing:
            return 2

    if buckets["missing_google_unseen"]:
        logger.warning(
            "SOFT WARN: %d google-OAuth users have no Supabase Auth row yet — "
            "they will be silently locked out post-cutover until they sign in via "
            "Supabase Google OAuth at least once",
            len(buckets["missing_google_unseen"]),
        )

    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Reconcile Mongo db.users vs Supabase Auth")
    p.add_argument("--fail-on-missing", action="store_true",
                   help="Exit non-zero (=2) if any email/password users are unmirrored")
    p.add_argument("--sample-limit", type=int, default=20,
                   help="Max sample rows to include per bucket in the JSON report")
    args = p.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    rc = asyncio.run(run(args.fail_on_missing, args.sample_limit))
    sys.exit(rc)


if __name__ == "__main__":
    main()
