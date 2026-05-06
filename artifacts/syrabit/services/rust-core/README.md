# services/rust-core

Rust core, packaged for Azure Container Apps. The
actual source lives at `backend/rust-core/` — this directory contains
only the DO-specific container build context.

The CI workflow `.github/workflows/do-deploy-rust-core.yml` builds
this image with `backend/rust-core` as the docker context (the build
step in CI passes the path explicitly so this folder doesn't need a
copy of the source tree).

Two ports:

| Port  | Purpose                                  |
|-------|------------------------------------------|
| 3000  | HTTP (REST + `/health` for App Platform) |
| 50051 | gRPC (in-VPC dial from `syrabit-backend`)|

Verify gRPC after deploy:

```sh
grpcurl -plaintext rust-core.<aca-suffix>.azurecontainerapps.io:50051 health.Check
```

See `docs/infra/aca-cutover.md` for runbook details.
