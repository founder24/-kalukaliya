# Multi-stage Dockerfile for Syrabit Rust Core
# Produces a minimal production image

# Stage 1: Build stage
FROM rust:1.82-slim-bookworm AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

# Copy manifest files. Cargo.lock is checked in (Task #192 / #300) and is
# required for reproducible, locked builds; do NOT use a wildcard COPY here
# because that silently succeeds when the file is missing and would yield
# non-deterministic dependency resolution at image-build time.
COPY Cargo.toml Cargo.lock ./
COPY build.rs ./
COPY proto ./proto/

# Create dummy source to cache dependencies
RUN mkdir -p src/generated src/handlers src/services src/models src/db src/grpc \
    && echo "fn main() {}" > src/main.rs \
    && cargo build --release \
    && rm -rf src/*.rs

# Copy actual source code
COPY src ./src/

# Build the application
RUN touch src/main.rs && cargo build --release

# Stage 2: Runtime stage
FROM debian:bookworm-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy binary from builder
COPY --from=builder /app/target/release/rust-core /app/rust-core

# Copy migrations
COPY migrations ./migrations/

# Expose ports
EXPOSE 3000 50051

# Set environment variables
ENV RUST_LOG=info
ENV HTTP_PORT=3000
ENV GRPC_PORT=50051

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/health || exit 1

# Run the application
CMD ["./rust-core"]
