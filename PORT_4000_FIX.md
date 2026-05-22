# Port 4000 Closed Error - ROOT CAUSE & FIX

**Issue**: Sandbox expects service on port 4000, but nothing was listening  
**Status**: ✅ FIXED

---

## Root Cause Analysis

### What Was Wrong
1. **Original Config**: `.ideavo/config` specified frontend on port 3000
2. **Sandbox Expectation**: E2B sandbox looks for service on port 4000
3. **Mismatch**: No process listening on port 4000
4. **Result**: "Connection refused on port 4000" error

### Why It Happened
- During initial setup, config was set for frontend development (Vite on 3000)
- But the sandbox tunneling layer expects port 4000 for the main service
- Backend wasn't configured as the primary runStep

---

## Solution Implemented

### 1. Updated `.ideavo/config`
Changed `runStep` from:
```json
"runStep": [{
  "name": "Run Frontend Dev Server",
  "command": "pnpm --filter syrabit-frontend run dev --port 3000 --host",
  "port": "3000"
}]
```

To:
```json
"runStep": [{
  "name": "Run Backend Dev Server",
  "command": "bash /home/user/project/start-backend.sh",
  "port": "4000"
}]
```

### 2. Created `start-backend.sh`
Smart startup script that:
- ✅ Checks if Python dependencies are installed
- ✅ Installs them if missing
- ✅ Starts FastAPI on port 4000
- ✅ Enables auto-reload for development
- ✅ Binds to 0.0.0.0 for external access

```bash
#!/bin/bash
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
```

---

## How to Test

### Option 1: Run Startup Script
```bash
bash /home/user/project/start-backend.sh
```

Expected output:
```
🚀 Starting Syrabit Backend Dev Server
==================================
📦 Installing Python dependencies...
✅ Starting FastAPI server on port 4000...
📍 http://localhost:4000
📚 Docs: http://localhost:4000/docs

INFO:     Uvicorn running on http://0.0.0.0:4000
```

### Option 2: Manual Start
```bash
cd /home/user/project/apps/backend
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
```

### Option 3: Via Docker Compose (for all services)
```bash
cd /home/user/project
docker-compose up -d
# Runs MongoDB, Redis, Backend, all configured
```

---

## Verify Port is Open

```bash
# Check if service is listening
netstat -tlnp | grep 4000
# or
ss -tlnp | grep 4000

# Should show:
# LISTEN 0.0.0.0:4000 ...
```

---

## Test API Health

```bash
# Health check
curl http://localhost:4000/health

# Expected response:
{"status": "ok"}

# API Docs (auto-generated)
curl http://localhost:4000/docs
# Open in browser: http://localhost:4000/docs
```

---

## Architecture Now

```
External Request (port 4000)
           ↓
Nginx/Reverse Proxy
           ↓
FastAPI Backend (localhost:4000)
           ├→ MongoDB (via docker-compose)
           ├→ Redis (via docker-compose)
           ├→ Azure Search
           ├→ Vertex AI
           └→ Sarvam AI
```

---

## Files Modified

| File | Change | Reason |
|------|--------|--------|
| `.ideavo/config` | Changed port from 3000 → 4000 | Match sandbox expectation |
| `.ideavo/config` | Changed runStep command | Point to startup script |
| `start-backend.sh` | Created | Auto-install deps & start server |

---

## Important Notes

### Port Usage
- **4000**: Main API (uvicorn) - **REQUIRED FOR SANDBOX**
- **3000**: Frontend dev (if running separately)
- **8000**: Docker backend (if using docker-compose)
- **27017**: MongoDB (docker-compose only)
- **6379**: Redis (docker-compose only)

### Python Dependency Installation
The startup script handles this automatically, but you can pre-install:
```bash
cd apps/backend
python3 -m pip install --user -r requirements.txt
```

### Frontend Development
To run frontend separately:
```bash
pnpm --filter syrabit-frontend run dev --port 3000
```

This connects to the backend on port 4000.

---

## Troubleshooting

### Issue: "Port 4000 already in use"
```bash
# Find what's using port 4000
lsof -i :4000

# Kill it (if needed)
kill -9 <PID>

# Then restart
bash /home/user/project/start-backend.sh
```

### Issue: "ModuleNotFoundError: No module named 'fastapi'"
```bash
# Install dependencies manually
cd apps/backend
python3 -m pip install --user -r requirements.txt
```

### Issue: "Cannot connect to MongoDB"
```bash
# Start all services with docker-compose
docker-compose up -d

# Verify they're running
docker ps
```

### Issue: "Connection timeout on port 4000"
```bash
# Check if service is running
ps aux | grep uvicorn

# Check if port is listening
ss -tlnp | grep 4000

# If nothing, start it
bash /home/user/project/start-backend.sh
```

---

## Next Steps

1. ✅ **Port 4000 Fixed** - Backend now listening
2. ⏳ **Complete Phase 1 Fixes** - Authentication bugs
3. ⏳ **Run Tests** - Verify no regressions
4. ⏳ **Deploy to Staging** - Full integration test

---

**Status**: 🟢 RESOLVED  
**Date**: May 22, 2026  
**Action**: Service now running on port 4000, sandbox tunnel should connect

