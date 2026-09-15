---
name: Vite optimized dependency stability
description: Prevents dev-preview crashes caused by Vite discovering lazy staff/admin dependencies after the browser has loaded.
---

Vite development previews must pre-optimize dependencies used by lazy staff and admin routes, especially Radix UI packages. Late dependency discovery can invalidate the current optimized chunk and produce a dynamic-import failure that React reports as a misleading invalid-hook-call error.

**Why:** A staff-route lazy import triggered Vite's optimizer to reload after the app was already running; the browser requested the old chunk and the preview crashed until restart.

**How to apply:** When a lazy route imports a new dependency family, add those packages to the frontend `optimizeDeps.include` list and restart the workflow before browser verification.