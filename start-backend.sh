#!/bin/bash
set -e

echo "🚀 Starting Syrabit Backend Dev Server"
echo "=================================="

# Check if Python dependencies are installed
cd /home/user/project/apps/backend

if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "📦 Installing Python dependencies..."
    python3 -m pip install --quiet --user -r requirements.txt 2>&1 | tail -5 || true
fi

echo "✅ Starting FastAPI server on port 4000..."
echo "📍 http://localhost:4000"
echo "📚 Docs: http://localhost:4000/docs"
echo ""

# Start the server
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
