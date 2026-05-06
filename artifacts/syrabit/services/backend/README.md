# services/backend

Python FastAPI backend, packaged for Azure Container Apps
(Task #331). The actual application source lives at
`artifacts/syrabit-backend/` — this directory contains only the
DO-specific container build context.

The CI workflow `.github/workflows/do-deploy-backend.yml` uses this
folder as the Docker build context; it copies the FastAPI source into
the image at build time. To build locally:

```sh
docker build -f services/backend/Dockerfile -t syrabit-backend:dev \
  ../syrabit-backend
```

Health endpoint: `GET /api/health` on port `8080` (matches the
`targetPort` in `infra/azure/aca-syrabit-backend.bicep`).

See `docs/infra/aca-cutover.md` for the full deploy / scale / rollback
runbook.
