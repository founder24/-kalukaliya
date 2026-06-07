#!/bin/bash
set -e

echo "=== post-merge setup ==="

# Install root workspace deps (covers frontend, edge, mockup-sandbox)
echo "Installing pnpm workspace dependencies..."
pnpm install --frozen-lockfile

# Install backend Python deps if requirements changed
echo "Installing backend Python dependencies..."
cd apps/backend && pip install -q -r requirements.txt --no-deps 2>/dev/null || true; cd ../..

echo "=== post-merge setup complete ==="
