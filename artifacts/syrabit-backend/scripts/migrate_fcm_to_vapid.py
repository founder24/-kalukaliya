"""Task #557 — FCM → VAPID web-push migration runner.

Background
----------
The legacy push integration stored a Firebase Cloud Messaging (FCM)
registration token on each `db.push_subscriptions` doc as
``fcm_token`` / ``provider="fcm"`` / ``kind="fcm"``. The new self-hosted
path (Task #557) writes a W3C `PushSubscription` blob to
``subscription_info`` with ``provider="vapid"`` and dispatches via
`pywebpush` from the env-mounted VAPID private key.

This script is the operator-facing tool that walks the migration
window. It is **idempotent** and **safe to re-run** every day during
the rollout. It does NOT call FCM — Firebase has already been
decommissioned (no `firebase_admin` import remains in the tree); the
script only re-classifies and tombstones the legacy rows so the
`/admin/push/migration-status` dashboard reads correctly and
`_dispatch_push` does not fan out to a dead provider.

Why no FCM "reconnect" push?
----------------------------
The original Task #557 spec called for a final reconnect notification
delivered via FCM at window close, before tombstoning. Firebase was
already fully decommissioned in this codebase before #557 landed —
there is **no** `firebase_admin` import, no `FCM_SERVER_KEY`, no
service-account JSON, and the umbrella CI guard's `TODO_557_PATTERN`
now forbids reintroducing any of them. We therefore have **no
transport** through which to send a final FCM message, and adding a
short-lived Firebase project just to fire the reconnect ping would
violate V4 §12 ("no silent fallbacks") *and* the founder $100/mo cap
(Task #549). Operators are expected to surface the migration banner
through the in-app SW + the existing transactional email channel
(SES, post-Task #556) instead. This rationale is intentional and
final; do not re-add a Firebase code path here.

Lifecycle
---------
For each `db.push_subscriptions` doc whose `provider in {None, "fcm"}`
or whose top-level shape lacks a `subscription_info.endpoint`:

1. **classify** → mark `migration_state="pending"` + stamp
   `migration_first_seen_at` if absent.
2. **tombstone** when `now - migration_first_seen_at > MIGRATION_WINDOW`
   (default 30 days) → set `active=False`,
   `deactivated_at=<now>`,
   `deactivation_reason="fcm_migration_window_expired"`,
   `migration_state="tombstoned"`. The doc is retained for audit;
   `_dispatch_push` already filters on `active != False` so tombstoned
   rows stop receiving traffic immediately.
3. **purge** when `now - deactivated_at > MIGRATION_PURGE_GRACE`
   (default 7 days after tombstone, opt-in via `--purge`).

Docs that have *already* re-subscribed via the new VAPID flow are
detected by their valid `subscription_info.keys.p256dh` +
`subscription_info.keys.auth` and marked `migration_state="migrated"`
on first sweep so they show up in the dashboard's "migrated" bucket.

Usage
-----
    # Dry run (default — prints counts, makes no writes):
    python -m scripts.migrate_fcm_to_vapid

    # Execute the day-N sweep (classify + tombstone-if-due):
    python -m scripts.migrate_fcm_to_vapid --apply

    # End-of-window: also purge tombstones older than the grace period.
    python -m scripts.migrate_fcm_to_vapid --apply --purge

The script exits 0 on success, prints a JSON summary to stdout, and
emits the same numbers to the structured logger so the operator can
grep for them in CloudWatch / Sentry breadcrumbs. The numbers are also
exposed by `GET /api/admin/push/migration-status` for the admin
panel's migration progress card.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Allow `python scripts/migrate_fcm_to_vapid.py` from anywhere by adding
# the backend root to sys.path before the deps import.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from deps import db  # noqa: E402

logger = logging.getLogger("scripts.migrate_fcm_to_vapid")

MIGRATION_WINDOW = timedelta(days=int(os.environ.get("FCM_MIGRATION_WINDOW_DAYS", "30")))
MIGRATION_PURGE_GRACE = timedelta(days=int(os.environ.get("FCM_MIGRATION_PURGE_GRACE_DAYS", "7")))


def _is_legacy_fcm(doc: dict) -> bool:
    """A doc is legacy iff it carries an FCM-shaped marker OR lacks a
    valid W3C subscription_info blob."""
    provider = (doc.get("provider") or "").lower()
    kind = (doc.get("kind") or "").lower()
    if provider == "fcm" or kind == "fcm" or doc.get("fcm_token"):
        return True
    info = doc.get("subscription_info") or {}
    keys = info.get("keys") or {}
    if not info.get("endpoint") or not keys.get("p256dh") or not keys.get("auth"):
        return True
    return False


def _is_migrated(doc: dict) -> bool:
    """Doc has a complete W3C subscription_info — already on VAPID."""
    info = doc.get("subscription_info") or {}
    keys = info.get("keys") or {}
    return bool(info.get("endpoint") and keys.get("p256dh") and keys.get("auth"))


async def collect_status() -> dict[str, Any]:
    """Read-only count of every bucket. Used by both the script's
    summary line and the `/api/admin/push/migration-status` endpoint."""
    pending = migrated = tombstoned = active_total = 0
    cursor = db.push_subscriptions.find({}, {"_id": 0})
    async for doc in cursor:
        active_total += 1
        state = (doc.get("migration_state") or "").lower()
        if state == "tombstoned" or doc.get("active") is False:
            tombstoned += 1
        elif _is_migrated(doc) and not _is_legacy_fcm(doc):
            migrated += 1
        else:
            pending += 1
    return {
        "total":      active_total,
        "migrated":   migrated,
        "pending":    pending,
        "tombstoned": tombstoned,
        "window_days": MIGRATION_WINDOW.days,
        "purge_grace_days": MIGRATION_PURGE_GRACE.days,
        "as_of":      datetime.now(timezone.utc).isoformat(),
    }


async def run_sweep(apply: bool, purge: bool) -> dict[str, Any]:
    """One pass of the migration state machine."""
    now = datetime.now(timezone.utc)
    classified = tombstoned = purged = marked_migrated = 0

    # Keep `_id` so token-only legacy FCM rows (no endpoint at all)
    # can still be addressed for update/delete — they were the most
    # common Firebase shape pre-W3C and MUST be tombstoned with the
    # rest of the cohort.
    cursor = db.push_subscriptions.find({})
    async for doc in cursor:
        endpoint = doc.get("endpoint") or (doc.get("subscription_info") or {}).get("endpoint")
        # Build the address filter once. Prefer endpoint (stable, indexed),
        # fall back to _id for legacy token-only rows.
        if endpoint:
            addr = {"endpoint": endpoint}
        elif doc.get("_id") is not None:
            addr = {"_id": doc["_id"]}
        else:
            continue  # truly malformed — nothing to update

        # Already-tombstoned: handle purge if requested.
        state = (doc.get("migration_state") or "").lower()
        if state == "tombstoned":
            deactivated_at_raw = doc.get("deactivated_at")
            if purge and deactivated_at_raw:
                try:
                    deactivated_at = (
                        deactivated_at_raw if isinstance(deactivated_at_raw, datetime)
                        else datetime.fromisoformat(str(deactivated_at_raw))
                    )
                    if deactivated_at.tzinfo is None:
                        deactivated_at = deactivated_at.replace(tzinfo=timezone.utc)
                    if now - deactivated_at > MIGRATION_PURGE_GRACE:
                        if apply:
                            await db.push_subscriptions.delete_one(addr)
                        purged += 1
                except Exception as e:
                    logger.warning("Skipping purge for %s — bad deactivated_at: %s", endpoint, e)
            continue

        # New-shape doc that's complete: mark migrated on first sweep.
        if _is_migrated(doc) and not _is_legacy_fcm(doc):
            if state != "migrated":
                if apply:
                    await db.push_subscriptions.update_one(
                        addr,
                        {"$set": {
                            "migration_state": "migrated",
                            "migration_completed_at": now,
                        }},
                    )
                marked_migrated += 1
            continue

        # Legacy: classify or tombstone.
        first_seen_raw = doc.get("migration_first_seen_at")
        if first_seen_raw:
            try:
                first_seen = (
                    first_seen_raw if isinstance(first_seen_raw, datetime)
                    else datetime.fromisoformat(str(first_seen_raw))
                )
                if first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=timezone.utc)
            except Exception:
                first_seen = now
        else:
            first_seen = now
            if apply:
                await db.push_subscriptions.update_one(
                    addr,
                    {"$set": {
                        "migration_state": "pending",
                        "migration_first_seen_at": now,
                    }},
                )
            classified += 1
            continue

        if now - first_seen > MIGRATION_WINDOW:
            if apply:
                # Drop the legacy Firebase fields on tombstone so a
                # future schema audit doesn't trip over `fcm_token` /
                # `provider="fcm"` rows that nothing reads any more.
                await db.push_subscriptions.update_one(
                    addr,
                    {
                        "$set": {
                            "active": False,
                            "deactivated_at": now,
                            "deactivation_reason": "fcm_migration_window_expired",
                            "migration_state": "tombstoned",
                        },
                        "$unset": {
                            "fcm_token": "",
                            "provider": "",
                            "kind": "",
                        },
                    },
                )
            tombstoned += 1

    summary = {
        "apply":            apply,
        "purge":            purge,
        "classified":       classified,
        "marked_migrated":  marked_migrated,
        "tombstoned":       tombstoned,
        "purged":           purged,
        "window_days":      MIGRATION_WINDOW.days,
        "purge_grace_days": MIGRATION_PURGE_GRACE.days,
        "ran_at":           now.isoformat(),
    }
    logger.info("FCM→VAPID sweep summary: %s", summary)
    return summary


async def _amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Persist state changes (default is a dry run).")
    parser.add_argument("--purge", action="store_true",
                        help="Delete tombstoned docs older than the purge grace period.")
    parser.add_argument("--status-only", action="store_true",
                        help="Print the bucket counts and exit (read-only).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    if args.status_only:
        status = await collect_status()
        print(json.dumps(status, indent=2, default=str))
        return 0

    summary = await run_sweep(apply=args.apply, purge=args.purge)
    status = await collect_status()
    out = {"sweep": summary, "status_after": status}
    print(json.dumps(out, indent=2, default=str))
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
