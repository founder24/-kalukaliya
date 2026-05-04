# Rust Core on Digital Ocean — Droplet runbook

> Task #336 — self-managed Droplet alternative to the App-Platform
> spec at `.do/app-rust-core.yaml`. Pick this path when raw TCP gRPC
> on :50051 must be reachable from outside the DO VPC, or when the
> $6/mo Droplet pricing matters more than managed rolling deploys.

## What's in this folder

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs `rust-core` + Caddy on the Droplet. |
| `Caddyfile`          | Terminates TLS using the Cloudflare Origin CA cert mounted from the host. |
| `.env.example`       | Template for the Droplet's `/srv/rust-core/.env` (real values come from the team 1Password). |

## Initial setup

```sh
# 1. Create the Droplet inside the existing DO VPC so the Python
#    backend on App Platform can reach it via the private network.
doctl compute droplet create rust-core-blr1 \
  --region blr1 --size s-1vcpu-2gb \
  --image docker-20-04 --vpc-uuid "$DO_VPC_UUID" \
  --ssh-keys "$DO_SSH_KEY_FP" --enable-monitoring --wait

# 2. Open the firewall.
doctl compute firewall create --name rust-core-fw \
  --droplet-ids "$(doctl compute droplet list rust-core-blr1 --format ID --no-header)" \
  --inbound-rules "protocol:tcp,ports:80,sources:address:0.0.0.0/0 \
                   protocol:tcp,ports:443,sources:address:0.0.0.0/0 \
                   protocol:tcp,ports:50051,sources:address:0.0.0.0/0 \
                   protocol:tcp,ports:22,sources:address:$(curl -s ifconfig.me)/32" \
  --outbound-rules "protocol:tcp,ports:all,destinations:address:0.0.0.0/0 \
                    protocol:udp,ports:all,destinations:address:0.0.0.0/0"

# 3. Copy the deploy folder + .env onto the Droplet.
DROPLET_IP=$(doctl compute droplet get rust-core-blr1 --format PublicIPv4 --no-header)
ssh root@$DROPLET_IP "mkdir -p /srv/rust-core/certs"
scp -r ./*.yml Caddyfile root@$DROPLET_IP:/srv/rust-core/
scp .env       root@$DROPLET_IP:/srv/rust-core/.env
scp origin.pem origin.key root@$DROPLET_IP:/srv/rust-core/certs/

# 4. Log in to DOCR + start the stack.
ssh root@$DROPLET_IP <<'EOF'
  cd /srv/rust-core
  doctl registry login
  docker compose pull && docker compose up -d
EOF

# 5. Verify.
curl -fsS https://grpc.syrabit.ai/health   # → {"status":"ok"}
grpcurl -d '{}' grpc.syrabit.ai:50051 grpc.health.v1.Health/Check
```

## Updating the image

CI publishes a new tag on every merge. To roll forward:

```sh
ssh root@$DROPLET_IP "cd /srv/rust-core && \
  RUST_CORE_TAG=sha-<short-sha> docker compose pull rust-core && \
  RUST_CORE_TAG=sha-<short-sha> docker compose up -d rust-core"
```

Caddy keeps its sockets bound throughout, so the rolling
`docker compose up -d rust-core` is effectively zero-downtime for
the HTTP path. gRPC clients see ~2 s of `UNAVAILABLE` while the new
container binds :50051 — acceptable inside the cutover window.

## Rollback

```sh
ssh root@$DROPLET_IP "cd /srv/rust-core && \
  RUST_CORE_TAG=<previous-sha> docker compose up -d rust-core"
```

## Logs

```sh
ssh root@$DROPLET_IP "cd /srv/rust-core && docker compose logs --tail=200 -f rust-core"
```

For long-retention search use the Axiom dataset `rust-core-do-droplet`
configured via the `AXIOM_TOKEN` / `AXIOM_DATASET` env vars in `.env`.

## Decommission

```sh
ssh root@$DROPLET_IP "cd /srv/rust-core && docker compose down"
doctl compute firewall delete rust-core-fw
doctl compute droplet delete rust-core-blr1
```
