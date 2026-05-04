# Syrabit Backend — Digital Ocean (backend-local notes)

> Task #336 — backend-folder companion to the canonical runbook at
> [`../../docs/DIGITALOCEAN-DEPLOYMENT.md`](../../docs/DIGITALOCEAN-DEPLOYMENT.md).
> Use this file when you're already in `artifacts/syrabit-backend/`
> and need quick reminders about how the FastAPI image is shaped for
> the DO `syrabit-backend` App Platform service. The cutover & rollback
> runbook is [`../../docs/ops/digitalocean-cutover.md`](../../docs/ops/digitalocean-cutover.md).

## What changes for DO vs Railway

| Concern              | Railway (legacy)                                           | Digital Ocean App Platform (Task #336)                  |
|----------------------|------------------------------------------------------------|---------------------------------------------------------|
| Container build      | `railway up` (auto from Dockerfile)                        | `docker build` → DOCR push → `doctl apps update --spec` |
| Image registry       | Railway internal                                           | `registry.digitalocean.com/syrabit/syrabit-backend`     |
| Spec-as-code         | `railway.toml` (Railway-specific TOML)                     | `.do/app.yaml` (App Platform spec, version-controlled)  |
| Env vars             | `railway variables set …`                                  | `bash scripts/digitalocean.sh var-set syrabit-backend …` |
| Health check         | `healthcheckPath` in railway.toml                          | `health_check.http_path: /api/health` in `.do/app.yaml` |
| Rolling deploys      | Default                                                    | `deployment_strategy: ROLLING` in spec                  |
| Scaling              | Manual via dashboard                                       | `autoscaling.metrics.cpu.percent: 70` in spec           |
| Logs                 | `railway logs`                                             | `bash scripts/digitalocean.sh logs syrabit-backend`     |
| Public URL           | `*.up.railway.app`                                         | `*.ondigitalocean.app` (wrapped behind `api.syrabit.ai`) |

## Image notes

The `Dockerfile` in this folder is unchanged from the Railway era —
the same multi-stage Python 3.11-slim image is built by the DO
`digitalocean-deploy.yml` workflow and pushed to DOCR. The only
runtime delta is the **port**: DO defaults to `$PORT=8080` (set in
`.do/app.yaml`), where Railway used `$PORT=8000`. The Dockerfile's
`ENV PORT=8000` is overridden at runtime by the App Platform env var,
so no code change was required.

If you need to test the production image locally with the same
runtime port:

```sh
docker build -t syrabit-backend:dev .
docker run --rm -p 8080:8080 \
  -e PORT=8080 -e PYTHONUNBUFFERED=1 \
  --env-file ../../do-backend-vars.env \
  syrabit-backend:dev
curl http://localhost:8080/api/health
```

## See also

* `../../.do/app.yaml` — full env inventory + autoscaling config.
* `../../docs/DIGITALOCEAN-DEPLOYMENT.md` — first-time setup +
  scaling + rollback + common failure modes.
* `../../scripts/digitalocean.sh` — every operational subcommand.
