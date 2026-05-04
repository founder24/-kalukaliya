# Digital Ocean App Platform — bootstrap

Phase 1d (Task #330) sets up the empty App Platform shells for the
Python backend and the Rust core. Application code lands in the next
two tasks.

## Account prerequisites

1. Create the `syrabit` DO team and apply:
   - **Hatch** program credits (founder partner code on file).
   - **$200 trial credit** auto-applied to a fresh team.
2. Set a **billing alert at $50** and a **hard cap notification at
   $150** so a runaway deploy can never burn the trial pool.
3. Install and authenticate `doctl`:
   ```sh
   brew install doctl                       # or: snap install doctl
   doctl auth init --context syrabit
   doctl auth switch --context syrabit
   ```
4. Create a DigitalOcean Container Registry named `syrabit` in the
   same region (`blr1`) so App Platform pulls images over the local
   network:
   ```sh
   doctl registry create syrabit --region blr1 --subscription-tier basic
   doctl registry login
   ```

## Creating the empty apps

The two `app.yaml` specs in this directory are the source of truth. To
create the empty shells the very first time:

```sh
doctl apps create --spec infra/do/app-syrabit-backend.yaml
doctl apps create --spec infra/do/app-rust-core.yaml
```

After creation, capture the app IDs and store them as GitHub
repository variables (not secrets — they are not sensitive):

- `DO_APP_ID_SYRABIT_BACKEND`
- `DO_APP_ID_RUST_CORE`

CI will then update them in place via:

```sh
doctl apps update "$DO_APP_ID_SYRABIT_BACKEND" \
  --spec infra/do/app-syrabit-backend.yaml --wait
```

## Secrets

Stored at the GitHub **environment** level (`non-prod`, `prod`), not
at repo level, so production deploys require the configured reviewers
to approve:

| Secret              | Used by                              |
|---------------------|--------------------------------------|
| `DO_API_TOKEN`      | `do-deploy-*.yml` workflows          |
| `DO_REGISTRY_NAME`  | image push step (defaults to `syrabit`) |

AWS + Azure deploys use OIDC (no long-lived secrets) — see
`docs/infra/cicd.md`.
