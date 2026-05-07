#!/usr/bin/env python3
"""DNS alignment check for the Amazon SES sending domain.

Task #556 — SES is now the sole transactional email path. This script
replaces the legacy SendGrid-targeted check; the historical SendGrid
CNAMEs (`s1._domainkey`, `s2._domainkey`, link-branding `em####`) are
no longer published, and SES auto-issues its own DKIM/SPF chain
instead.

Validates four things over plain DNS lookup (`dig +short`):

  1. SPF on the parent domain (`syrabit.ai`) authorises Amazon SES via
     the standard `include:amazonses.com` directive.
  2. The SES MAIL FROM subdomain (default `mail.syrabit.ai`) has both
     halves of the alignment chain published:
       - the MAIL FROM MX (`feedback-smtp.<region>.amazonses.com`)
       - the MAIL FROM SPF TXT (`v=spf1 include:amazonses.com -all`)
  3. The three SES EasyDKIM CNAMEs published under the parent domain
     (`<token>._domainkey.syrabit.ai` → `<token>.dkim.amazonses.com`).
     The three tokens are rotated by SES; pass them via
     `--dkim-token` (repeatable, must be supplied 3 times).
  4. DMARC on the parent domain has a `v=DMARC1` record with
     `p=quarantine` or stricter (warns, does not fail, on `p=none`),
     and the `rua` mailbox is set to a real address (not blank).

Exit codes:
  0  all four checks pass
  1  one or more checks failed
  2  harness failure (no `dig`, network down)
  3  usage error
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

DEFAULT_DOMAIN = "syrabit.ai"
DEFAULT_MAIL_FROM = "mail.syrabit.ai"
DEFAULT_REGION = "us-east-1"


def _dig(name: str, rtype: str) -> list[str]:
    if shutil.which("dig") is None:
        raise RuntimeError("`dig` CLI not on PATH")
    out = subprocess.run(
        ["dig", "+short", name, rtype],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        raise RuntimeError(f"dig {name} {rtype} failed: {out.stderr.strip()}")
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def check_spf(domain: str) -> tuple[bool, str]:
    txts = _dig(domain, "TXT")
    spf = next((t for t in txts if "v=spf1" in t.lower()), "")
    if not spf:
        return False, f"no SPF record on {domain}"
    if "include:amazonses.com" not in spf.lower():
        return False, f"SPF on {domain} does not include amazonses.com → {spf[:120]}"
    return True, "SPF includes amazonses.com"


def check_mail_from(mail_from: str, region: str) -> tuple[bool, str]:
    mxs = _dig(mail_from, "MX")
    expected_mx = f"feedback-smtp.{region}.amazonses.com"
    if not any(expected_mx in m.lower() for m in mxs):
        return False, (
            f"{mail_from} MX missing {expected_mx} (got {mxs or '∅'})"
        )
    txts = _dig(mail_from, "TXT")
    if not any("v=spf1" in t.lower() and "amazonses.com" in t.lower()
               for t in txts):
        return False, f"{mail_from} SPF TXT missing amazonses.com"
    return True, f"MAIL FROM {mail_from} aligned ({region})"


def check_dkim(domain: str, tokens: list[str]) -> tuple[bool, str]:
    if len(tokens) != 3:
        return False, f"need exactly 3 DKIM tokens (got {len(tokens)})"
    missing = []
    for tok in tokens:
        name = f"{tok}._domainkey.{domain}"
        cnames = _dig(name, "CNAME")
        target = f"{tok}.dkim.amazonses.com"
        if not any(target in c.lower() for c in cnames):
            missing.append(name)
    if missing:
        return False, f"missing/wrong DKIM CNAMEs: {missing}"
    return True, "all 3 SES DKIM CNAMEs aligned"


def check_dmarc(domain: str) -> tuple[bool, str]:
    txts = _dig(f"_dmarc.{domain}", "TXT")
    rec = next((t for t in txts if "v=dmarc1" in t.lower()), "")
    if not rec:
        return False, f"_dmarc.{domain} TXT missing"
    low = rec.lower()
    if "p=quarantine" not in low and "p=reject" not in low:
        return False, f"DMARC policy too weak (need quarantine|reject) → {rec[:120]}"
    if "rua=mailto:" not in low or "rua=mailto:," in low:
        return False, "DMARC rua mailbox unset — aggregate reports will go nowhere"
    return True, "DMARC quarantine|reject + rua set"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--mail-from", default=DEFAULT_MAIL_FROM)
    ap.add_argument("--region", default=DEFAULT_REGION,
                    help="SES region for MAIL FROM MX (us-east-1 | ap-south-1)")
    ap.add_argument("--dkim-token", action="append", default=[],
                    help="SES EasyDKIM token (repeat 3x)")
    args = ap.parse_args()

    if len(args.dkim_token) != 3:
        print("usage error: pass --dkim-token THREE times "
              "(values from `terraform output -json ses_dkim_cname_records`)",
              file=sys.stderr)
        return 3

    try:
        results = [
            ("SPF",       check_spf(args.domain)),
            ("MAIL FROM", check_mail_from(args.mail_from, args.region)),
            ("DKIM",      check_dkim(args.domain, args.dkim_token)),
            ("DMARC",     check_dmarc(args.domain)),
        ]
    except RuntimeError as exc:
        print(f"HARNESS FAIL: {exc}", file=sys.stderr)
        return 2

    failed = 0
    for label, (ok, detail) in results:
        marker = "PASS" if ok else "FAIL"
        print(f"{marker:4} {label:9} {detail}")
        if not ok:
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
