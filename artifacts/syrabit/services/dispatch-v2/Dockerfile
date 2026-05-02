# syntax=docker/dockerfile:1
# ─── Build stage ──────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM node:20-alpine AS runtime

ENV NODE_ENV=production \
    PORT=8080

WORKDIR /app

COPY --from=builder /app/dist ./dist

# No runtime npm dependencies — service uses only Node.js built-ins.
# If deps are ever added, copy package.json and run `npm ci --omit=dev`.

EXPOSE 8080

# Run as non-root user for gVisor / Confidential Computing compatibility.
USER node

CMD ["node", "dist/index.js"]
