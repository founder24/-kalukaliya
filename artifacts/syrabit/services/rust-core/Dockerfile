# services/rust-core/Dockerfile
#
# Production image for the Rust core on DO App Platform (Task #331).
# Mirrors `backend/rust-core/Dockerfile` with the following tweaks
# specific to App Platform:
#
#   1. Two ports are EXPOSEd (3000 HTTP, 50051 gRPC) and both are
#      declared in `infra/do/app-rust-core.yaml` as `http_port` and
#      `internal_ports`. App Platform terminates HTTP/2 at the LB so
#      gRPC works without a Droplet.
#   2. The /health endpoint is on HTTP_PORT — same path the spec's
#      `health_check.http_path` probes.

FROM rust:1.82-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

# Cargo.lock is checked in — required for reproducible, locked builds.
COPY Cargo.toml Cargo.lock ./
COPY build.rs ./
COPY proto ./proto/

# Cache dependencies via a dummy main.
RUN mkdir -p src/generated src/handlers src/services src/models src/db src/grpc \
    && echo "fn main() {}" > src/main.rs \
    && cargo build --release \
    && rm -rf src/*.rs

COPY src ./src/

RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/rust-core /app/rust-core
COPY migrations ./migrations/

EXPOSE 3000 50051

ENV RUST_LOG=info \
    HTTP_PORT=3000 \
    GRPC_PORT=50051

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider "http://localhost:${HTTP_PORT}/health" || exit 1

CMD ["./rust-core"]
