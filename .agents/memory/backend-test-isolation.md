---
name: Backend test isolation
description: Prevent production startup work and Python module identity leaks from making backend tests flaky.
---

## Endpoint test lifespans
For endpoint-only tests with mocked providers, bypass the production application lifespan. Keep explicit lifespan tests for startup behavior.

**Why:** TestClient's normal lifespan can launch provider warmups and background tasks unrelated to the endpoint assertion; cancellation during shutdown can then stall a latency test.

**How to apply:** Use a scoped no-op `app.router.lifespan_context` in endpoint latency tests, while mocking the endpoint's I/O boundaries.

## Canonical importer modules
Do not load `scripts.ahsec_ingest` with `spec_from_file_location` under its canonical dotted name and overwrite `sys.modules`. Import it normally and patch the shared module object. For import-failure coverage, intercept the specific `builtins.__import__` call instead of removing or replacing the module entry.

**Why:** Replacing only `sys.modules` can leave the parent `scripts` package pointing to another module object. Production imports may then call real providers or database code while tests patch a stale object.

**How to apply:** Add the backend root to `sys.path` only when needed, use `import scripts.ahsec_ingest`, and scope any import interception to the single test.