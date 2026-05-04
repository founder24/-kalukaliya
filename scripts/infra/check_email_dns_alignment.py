#!/usr/bin/env python3
"""
DNS alignment check for the SendGrid sending domain (Task #364 §1
gate 8 + §7 smoke row 4).

Validates four things, all over plain DNS lookup (`dig +short`):

  1. SPF on the parent domain (`syrabit.ai`) authorises SendGrid via
     the standard `include:sendgrid.net` directive (or via
     `include:em.syrabit.ai` if the operator chose the subdomain
     delegation pattern).
  2. The SendGrid sending subdomain (default `em.syrabit.ai`) has
     **all three** CNAMEs SendGrid auto-issues when Domain
     Authentication ("Authenticated Domain") is set up:
       - `s1._domainkey.<domain>` and `s2._domainkey.<domain>` for
         DKIM signing
       - the link-branding CNAME at the operator-supplied prefix
         (typically `em####.<domain>`; the exact `em####` varies per
         account and is shown in SendGrid → Settings → Sender
         Authentication). This is the third CNAME flagged by the
         #364 review as missing from the earlier draft.
     The script only checks that the CNAME targets exist and end in
     `.sendgrid.net.`. The link-branding check is gated on the
     operator passing `--link-branding-cname`; if omitted the
     script emits a WARN (not FAIL) so existing operators on
     Automated Security but without explicit link branding still
     get a green DKIM/SPF/DMARC result.
  3. DMARC on the parent domain has a `v=DMARC1` record with
     `p=quarantine` or stricter (warns, does not fail, on `p=none`).
  4. The DMARC record's `rua` mailbox is not the SendGrid default
     placeholder (`postmaster@em.syrabit.ai` or unset → operator
     would never see aggregate reports).

Exit codes:
  0  all four checks pass
  1  one or more checks failed
  2  harness failure (no `dig`, network down)
  3  usage error
"""

import argparse
import shutil
import subprocess
import sys


def _dig(name: str, rtype: str) -> list[str]:
    if shutil.which("dig") is None:
        raise RuntimeError("`dig` CLI not on PATH")
    out = subprocess.run(
        ["dig", "+short", "+time=5", "+tries=2", name, rtype],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(
            f"dig {name} {rtype} failed: {out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _txt_strings(rows: list[str]) -> list[str]:
    out = []
    for row in rows:
        if not row:
            continue
        if row.startswith('"') and row.endswith('"'):
            out.append(row[1:-1].replace('" "', ''))
        else:
            out.append(row)
    return out


def _check_spf(parent: str, sending_subdomain: str) -> tuple[bool, str]:
    txt = _txt_strings(_dig(parent, "TXT"))
    spf_rows = [t for t in txt if t.lower().startswith("v=spf1")]
    if not spf_rows:
        return (False, f"no SPF record on `{parent}`")
    if len(spf_rows) > 1:
        return (False,
                f"multiple SPF records on `{parent}` "
                f"(RFC 7208 forbids more than one)")
    spf = spf_rows[0].lower()
    if "include:sendgrid.net" in spf:
        return (True, f"SPF includes sendgrid.net on `{parent}`")
    if f"include:{sending_subdomain.lower()}" in spf:
        return (True,
                f"SPF includes `{sending_subdomain}` (subdomain "
                f"delegation pattern) on `{parent}`")
    return (False,
            f"SPF on `{parent}` authorises neither sendgrid.net "
            f"nor `{sending_subdomain}`")


def _check_dkim_cnames(sending_subdomain: str) -> tuple[bool, str]:
    targets = []
    for label in ("s1._domainkey", "s2._domainkey"):
        rows = _dig(f"{label}.{sending_subdomain}", "CNAME")
        if not rows:
            return (False,
                    f"no CNAME at `{label}.{sending_subdomain}` "
                    f"(SendGrid DKIM signing key missing)")
        targets.extend(rows)
    bad = [t for t in targets if not t.lower().rstrip(".").endswith(
        ".sendgrid.net")]
    if bad:
        return (False,
                f"DKIM CNAMEs on `{sending_subdomain}` do not point at "
                f".sendgrid.net targets: {bad}")
    return (True,
            f"DKIM CNAMEs on `{sending_subdomain}` "
            f"({', '.join(targets)}) all chain into .sendgrid.net")


def _check_link_branding(fqdn: str) -> tuple[bool, str]:
    """Validate the SendGrid link-branding CNAME (the third CNAME
    SendGrid issues for Domain Authentication, alongside the two
    DKIM CNAMEs). Operator supplies the exact FQDN because the
    `em####` prefix is account-specific."""
    rows = _dig(fqdn, "CNAME")
    if not rows:
        return (False,
                f"no CNAME at `{fqdn}` (SendGrid link-branding "
                f"record missing — link-tracking will fall back to "
                f"sendgrid.net URLs in delivered email, which "
                f"confuses recipients and reduces engagement)")
    bad = [t for t in rows if not t.lower().rstrip(".").endswith(
        ".sendgrid.net")]
    if bad:
        return (False,
                f"link-branding CNAME on `{fqdn}` does not point "
                f"at a .sendgrid.net target: {bad}")
    return (True,
            f"link-branding CNAME on `{fqdn}` "
            f"({', '.join(rows)}) chains into .sendgrid.net")


def _check_dmarc(parent: str) -> tuple[bool, str, list[str]]:
    txt = _txt_strings(_dig(f"_dmarc.{parent}", "TXT"))
    dmarc_rows = [t for t in txt if t.lower().startswith("v=dmarc1")]
    if not dmarc_rows:
        return (False, f"no DMARC record at `_dmarc.{parent}`", [])
    dmarc = dmarc_rows[0]
    parts = {seg.split("=", 1)[0].strip().lower():
             seg.split("=", 1)[1].strip()
             for seg in dmarc.split(";") if "=" in seg}
    p = parts.get("p", "").lower()
    rua = parts.get("rua", "")
    warnings: list[str] = []
    if p == "none":
        warnings.append(
            f"DMARC policy is `p=none` — recommend tightening to "
            f"`p=quarantine` once SendGrid warmup §3 finalizes")
    elif p not in ("quarantine", "reject"):
        return (False,
                f"DMARC `p=` is unrecognised value `{p}` "
                f"(expected none/quarantine/reject)", warnings)
    if not rua or "postmaster@em." in rua:
        warnings.append(
            f"DMARC `rua` mailbox is missing or set to the SendGrid "
            f"default placeholder — operator will never see "
            f"aggregate reports")
    return (True, f"DMARC on `{parent}` has p={p or 'unset'}, "
                  f"rua={rua or 'unset'}", warnings)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--domain", required=True,
                   help="SendGrid sending subdomain "
                        "(e.g. em.syrabit.ai)")
    p.add_argument("--parent", default="",
                   help="Parent domain for SPF + DMARC. Defaults to "
                        "stripping the leftmost label of --domain.")
    p.add_argument("--link-branding-cname", default="",
                   help="Full FQDN of the SendGrid link-branding "
                        "CNAME (e.g. em1234.em.syrabit.ai). The "
                        "`em####` prefix is shown in SendGrid → "
                        "Settings → Sender Authentication. If "
                        "omitted, the link-branding check is "
                        "skipped with a WARN.")
    args = p.parse_args()

    parent = args.parent.strip() or args.domain.split(".", 1)[1]
    if "." not in parent:
        print(f"ERROR: cannot derive parent domain from `{args.domain}`; "
              f"pass --parent explicitly", file=sys.stderr)
        return 3

    print(f"Checking SPF/DKIM/DMARC alignment for `{args.domain}` "
          f"under parent `{parent}`")
    print()

    failed = False
    try:
        ok, msg = _check_spf(parent, args.domain)
        print(f"  [{'OK' if ok else 'FAIL'}] SPF: {msg}")
        failed = failed or not ok

        ok, msg = _check_dkim_cnames(args.domain)
        print(f"  [{'OK' if ok else 'FAIL'}] DKIM: {msg}")
        failed = failed or not ok

        ok, msg, warnings = _check_dmarc(parent)
        print(f"  [{'OK' if ok else 'FAIL'}] DMARC: {msg}")
        for w in warnings:
            print(f"  [WARN] DMARC: {w}")
        failed = failed or not ok

        if args.link_branding_cname:
            ok, msg = _check_link_branding(args.link_branding_cname)
            print(f"  [{'OK' if ok else 'FAIL'}] LinkBranding: {msg}")
            failed = failed or not ok
        else:
            print(f"  [WARN] LinkBranding: skipped — pass "
                  f"--link-branding-cname <fqdn> to validate the "
                  f"third SendGrid CNAME")

    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print()
    print("FAIL — fix the rows marked [FAIL] above" if failed
          else "OK — SendGrid DNS alignment is good")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
