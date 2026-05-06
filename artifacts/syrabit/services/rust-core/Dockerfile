# services/rust-core/Dockerfile
#
# Production image for the Rust core on Azure Container Apps.
# Mirrors `backend/rust-core/Dockerfile` with the following tweaks
# specific to App Platform:
#
#   1. Two ports are EXPOSEd (3000 HTTP, 50051 gRPC) and both are
#      declared in the rust-core ACA bicep template as `targetPort` and
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

# Cargo.lock IS checked in for reproducible, locked builds (the
# image build runs `cargo build --release --locked` below). When
# bumping a direct dep in Cargo.toml, regenerate the lockfile via
# `cargo update -p <crate>` and commit the diff.
COPY Cargo.toml Cargo.lock ./
COPY build.rs ./
# `proto/` is created on demand by the build script when user-defined
# .proto files are added; copy it conditionally so the image still
# builds in the empty-proto state.
RUN mkdir -p proto

# Cache dependencies via a dummy main. `--locked` enforces the
# committed Cargo.lock; the build fails fast if the lockfile and
# the manifest are out of sync.
RUN mkdir -p src/generated src/handlers src/services src/models src/db src/grpc \
    && echo "fn main() {}" > src/main.rs \
    && cargo build --release --locked \
    && rm -rf src/*.rs

COPY src ./src/

RUN touch src/main.rs && cargo build --release --locked

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
