#!/usr/bin/env python3
"""Sync all local-DB users to Supabase Auth.

As of Task #156, Supabase is the sole auth provider.  The frontend login
calls supabase.auth.signInWithPassword() — so any user without a Supabase
Auth account cannot sign in.  This script creates Supabase Auth entries for
every user in the local users table who doesn't already have one, then sends
each a password-reset link so they can regain access.

Usage (run from the backend root):
    python scripts/sync_users_to_supabase.py              # live run
    python scripts/sync_users_to_supabase.py --dry-run    # preview only, no changes
    python scripts/sync_users_to_supabase.py --no-email   # create accounts, skip emails

Google OAuth users (auth_provider='google') are skipped — Supabase creates
their accounts automatically on first Google sign-in.
"""
import asyncio
import sys
import os
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_users_to_supabase")


async def _fetch_all_local_users() -> list[dict]:
    """Return every row from the Mongo users collection.

    Task #47 destructive-PR fix (2026-05-09): the original implementation
    read from a Postgres `users` table that does not exist in production
    (production runs on MongoDB Atlas via `deps.db`, see
    `verify_supabase_mirror.py`). With the legacy reader the script
    silently no-op'd and reported "Created: 0", which would have given
    a false-green to the cutover gate. We now mirror the
    reconciliation script: every active user with a non-empty email is
    a candidate, and Google-OAuth / banned rows are filtered later in
    `run_sync` to keep the bucketing logic identical to the gate.
    """
    from deps import db
    if db is None:
        raise RuntimeError("Mongo client not initialised — check MONGO_URL")
    cursor = db.users.find(
        {"status": {"$ne": "banned"}, "email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "email": 1, "name": 1, "auth_provider": 1,
         "is_admin": 1, "status": 1},
    )
    return [r async for r in cursor]


def _fetch_all_supabase_auth_emails(supa_client) -> set[str]:
    """Return the set of emails already registered in Supabase Auth (paginated)."""
    emails: set[str] = set()
    page = 1
    per_page = 1000
    while True:
        try:
            result = supa_client.auth.admin.list_users(page=page, per_page=per_page)
            users = result if isinstance(result, list) else getattr(result, "users", result)
        except Exception as exc:
            logger.warning("list_users page %d failed: %s", page, exc)
            break
        if not users:
            break
        for u in users:
            email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            if email:
                emails.add(email.lower().strip())
        if len(users) < per_page:
            break
        page += 1
    return emails


def _create_supabase_user(supa_client, email: str, name: str) -> tuple[bool, str]:
    """
    Create a Supabase Auth user with email_confirm=True (no password set yet).
    Returns (success, error_message).
    """
    try:
        supa_client.auth.admin.create_user({
            "email": email,
            "email_confirm": True,
            "user_metadata": {"name": name or ""},
        })
        return True, ""
    except Exception as exc:
        msg = str(exc)
        if "already been registered" in msg or "already exists" in msg or "duplicate" in msg.lower():
            return True, "already_exists"
        return False, msg


def _generate_recovery_link(supa_client, email: str) -> str | None:
    """Generate a password-reset link for the given email. Returns the URL or None.

    Task #47 fix (2026-05-09): the previous extractor only checked
    `result.action_link` (attr) and `props.get(...)` (dict). The
    supabase-py SDK actually returns a Pydantic model where the link
    lives at ``result.properties.action_link`` — every previous live
    sync logged "Could not generate recovery link" on a 200 OK
    response, so 9 newly-created Supabase Auth users got 0 password-set
    emails and were locked out. We now walk: attr → dict → nested
    properties.action_link, and fall back to wrapping `hashed_token`
    in the standard Supabase recovery URL when only the raw token is
    returned.
    """
    try:
        result = supa_client.auth.admin.generate_link({
            "type": "recovery",
            "email": email,
        })
    except Exception as exc:
        logger.warning("generate_link failed for %s: %s", email, exc)
        return None

    def _extract(container) -> str | None:
        if container is None:
            return None
        # attr access (Pydantic model)
        link = getattr(container, "action_link", None)
        if link:
            return link
        # dict access
        if isinstance(container, dict):
            return container.get("action_link")
        return None

    # 1) result.action_link directly (older SDK shape)
    link = _extract(result)
    if link:
        return link
    # 2) result.properties.action_link (current SDK shape — Pydantic model)
    props = getattr(result, "properties", None)
    if props is None and isinstance(result, dict):
        props = result.get("properties")
    link = _extract(props)
    if link:
        return link
    # 3) fall back to wrapping hashed_token in the canonical recovery URL
    hashed = (
        getattr(props, "hashed_token", None)
        if props is not None and not isinstance(props, dict)
        else (props or {}).get("hashed_token") if isinstance(props, dict) else None
    )
    if hashed:
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        if base:
            return f"{base}/auth/v1/verify?token={hashed}&type=recovery"
    logger.warning(
        "Could not extract action_link from generate_link response for %s "
        "(result type=%s, props type=%s)",
        email, type(result).__name__, type(props).__name__ if props else "None",
    )
    return None


def _send_reset_email(to: str, name: str, reset_link: str):
    """Send a branded password-setup email via the existing email infrastructure.

    Task #47 fix (2026-05-09): the previous import (`_send_sync`) does
    not exist in `email_templates` — every send silently failed and
    counted as a success in `stats`. We now use `_send_via_ses` which
    is the synchronous SES helper used by every other transactional
    template path (`send_password_reset`, `send_plan_activation`).
    """
    try:
        from email_templates import _send_via_ses, _base, _button, _BRAND, _MUTED

        body = _base(f"""
          <h2 style="color:{_BRAND};margin:0 0 8px;">Set your Syrabit.ai password</h2>
          <p style="color:{_MUTED};margin:0 0 20px;">
            Hi {name or 'there'},
          </p>
          <p style="margin:0 0 20px;">
            We have upgraded our sign-in system. To continue using Syrabit.ai,
            please set a new password using the button below. Your study history,
            credits, and all account data are intact.
          </p>
          <p style="margin-bottom:24px;">
            {_button("Set my password", reset_link)}
          </p>
          <p style="color:{_MUTED};font-size:12px;margin:0;">
            This link expires in 24 hours. If you sign in with Google, you can
            ignore this email — your Google account is already linked.
          </p>
        """)
        _send_via_ses(to, "Action required: set your Syrabit.ai password", body)
    except Exception as exc:
        logger.warning("Failed to send reset email to %s: %s", to, exc)
        raise


def _send_recovery_via_supabase_smtp(supa_anon_client, email: str) -> tuple[bool, str]:
    """Trigger Supabase's built-in password-reset email.

    Task #47 (2026-05-09): when --use-supabase-email is set, we ask
    Supabase itself to dispatch the recovery email via the project's
    configured SMTP (Settings → Auth → SMTP). This bypasses our SES
    setup entirely — required when running the migration from a
    sandbox-SES environment that cannot send to unverified addresses.

    Uses the *anon* Supabase client because `reset_password_for_email`
    is a public-side method, not an admin one.
    """
    try:
        # supabase-py exposes this as either reset_password_email or
        # reset_password_for_email depending on version.
        client = supa_anon_client.auth
        fn = getattr(client, "reset_password_for_email", None) or getattr(
            client, "reset_password_email", None
        )
        if fn is None:
            return False, "supabase-py client missing reset_password_for_email"
        fn(email)
        return True, ""
    except Exception as exc:
        return False, str(exc)


async def run_sync(dry_run: bool = False, skip_email: bool = False, resend_emails: bool = False, use_supabase_email: bool = False):
    from supabase import create_client as _create_supa

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    supabase_anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        sys.exit(1)
    if use_supabase_email and not supabase_anon:
        logger.error("--use-supabase-email requires SUPABASE_ANON_KEY")
        sys.exit(1)

    supa_client = _create_supa(supabase_url, supabase_key)
    supa_anon_client = _create_supa(supabase_url, supabase_anon) if use_supabase_email else None

    logger.info("Fetching all active users from MongoDB…")
    local_users = await _fetch_all_local_users()
    logger.info("  Found %d local users", len(local_users))

    logger.info("Fetching existing Supabase Auth users…")
    existing_emails = await asyncio.to_thread(_fetch_all_supabase_auth_emails, supa_client)
    logger.info("  Found %d users already in Supabase Auth", len(existing_emails))

    stats = {"created": 0, "skipped_google": 0, "skipped_exists": 0, "error": 0, "email_sent": 0}

    for user in local_users:
        email = (user.get("email") or "").lower().strip()
        name  = user.get("name") or ""
        provider = user.get("auth_provider") or "email"
        status   = user.get("status") or "active"

        if not email:
            logger.warning("  SKIP (no email): id=%s", user.get("id"))
            continue

        if status == "banned":
            logger.info("  SKIP (banned): %s", email)
            continue

        if provider == "google":
            logger.info("  SKIP (google oauth — handled on first login): %s", email)
            stats["skipped_google"] += 1
            continue

        if email in existing_emails:
            stats["skipped_exists"] += 1
            if resend_emails and not skip_email:
                if dry_run:
                    logger.info("  [DRY-RUN] Would resend recovery email: %s", email)
                    stats["email_sent"] += 1
                    continue
                if use_supabase_email:
                    ok, err = await asyncio.to_thread(
                        _send_recovery_via_supabase_smtp, supa_anon_client, email
                    )
                    if ok:
                        logger.info("  RESENT (via Supabase SMTP): %s", email)
                        stats["email_sent"] += 1
                    else:
                        logger.error("  ERROR Supabase SMTP send for %s: %s", email, err)
                        stats["error"] += 1
                    continue
                link = await asyncio.to_thread(_generate_recovery_link, supa_client, email)
                if link:
                    await asyncio.to_thread(_send_reset_email, email, name, link)
                    logger.info("  RESENT password-set email: %s", email)
                    stats["email_sent"] += 1
                else:
                    logger.error("  ERROR could not generate recovery link for %s", email)
                    stats["error"] += 1
            else:
                logger.info("  SKIP (already in Supabase Auth): %s", email)
            continue

        if dry_run:
            logger.info("  [DRY-RUN] Would create Supabase Auth user: %s", email)
            stats["created"] += 1
            continue

        ok, err = await asyncio.to_thread(_create_supabase_user, supa_client, email, name)
        if not ok:
            logger.error("  ERROR creating %s: %s", email, err)
            stats["error"] += 1
            continue

        if err == "already_exists":
            logger.info("  SKIP (already in Supabase Auth, concurrent): %s", email)
            stats["skipped_exists"] += 1
            continue

        logger.info("  CREATED Supabase Auth user: %s", email)
        stats["created"] += 1
        existing_emails.add(email)

        if skip_email:
            continue

        link = await asyncio.to_thread(_generate_recovery_link, supa_client, email)
        if link:
            await asyncio.to_thread(_send_reset_email, email, name, link)
            logger.info("  EMAIL SENT (password-set link): %s", email)
            stats["email_sent"] += 1
        else:
            logger.warning("  Could not generate recovery link for %s", email)

    logger.info("")
    logger.info("=== Migration complete ===")
    logger.info("  Created (or would create): %d", stats["created"])
    logger.info("  Already in Supabase Auth:  %d", stats["skipped_exists"])
    logger.info("  Google OAuth (skipped):    %d", stats["skipped_google"])
    logger.info("  Errors:                    %d", stats["error"])
    if not skip_email:
        logger.info("  Password-set emails sent:  %d", stats["email_sent"])
    logger.info("")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync local users to Supabase Auth")
    parser.add_argument("--dry-run",   action="store_true", help="Preview only — make no changes")
    parser.add_argument("--no-email",  action="store_true", help="Create accounts but skip sending emails")
    parser.add_argument(
        "--resend-emails", action="store_true",
        help="Also re-issue a password-recovery email to candidates that already exist in Supabase Auth "
             "(used by Task #47 to recover users created by an earlier broken sync run).",
    )
    parser.add_argument(
        "--use-supabase-email", action="store_true",
        help="Dispatch the recovery email via Supabase project SMTP (Settings → Auth → SMTP) instead of "
             "AWS SES. Required when running from an environment whose SES is in sandbox mode.",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    asyncio.run(run_sync(
        dry_run=args.dry_run,
        skip_email=args.no_email,
        resend_emails=args.resend_emails,
        use_supabase_email=args.use_supabase_email,
    ))


if __name__ == "__main__":
    main()
