"""
Sync GOOGLE_APPLICATION_CREDENTIALS_JSON from GCP Secret Manager → CF Worker secret (GOOGLE_SA_KEY).
Uses the Cloudflare REST API directly so wrangler stdin parsing issues with large JSON blobs are avoided.

Usage: python3 scripts/sync_cf_sa_key.py <sa_key_value>
Env:   CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
"""
import json
import sys
import urllib.request
import os

def main():
    if len(sys.argv) < 2:
        print("Usage: sync_cf_sa_key.py <sa_key_value>", file=sys.stderr)
        sys.exit(1)

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    val = sys.argv[1]

    if not token or not account_id:
        print("ERROR: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set", file=sys.stderr)
        sys.exit(1)

    worker = "syrabitworker-prod"
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/workers/scripts/{worker}/secrets"
    )
    body = json.dumps({
        "name": "GOOGLE_SA_KEY",
        "text": val,
        "type": "secret_text",
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())

    if resp.get("success"):
        print("OK GOOGLE_SA_KEY synced to CF Worker via REST API")
    else:
        print(f"FAIL: {resp.get('errors', '')}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
