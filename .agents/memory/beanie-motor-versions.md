---
name: Beanie/Motor version pinning
description: Required version combination for beanie ODM with motor async driver in this project.
---

# Beanie / Motor / PyMongo versions

**Rule:** Pin `beanie==1.30.0`. Do not upgrade to beanie 2.x without also upgrading motor.

**Why:**
- beanie 2.x calls `database.client.append_metadata(_DRIVER_METADATA)` — motor 3.7.1's `AsyncIOMotorClient` does not expose this method → `TypeError: MotorDatabase object is not callable`.
- beanie 1.30.0 with motor 3.x: some internal code did `if not db:` / `bool(database)` — motor 3.x forbids that → `TypeError: Database objects do not implement truth value testing`. Fixed in `mongo.py` by changing `if not db:` → `if db is None:`.
- Current working combination: `beanie==1.30.0`, `motor==3.7.1`, `pymongo==4.17.0`.

**How to apply:**
- requirements.txt already pins `beanie==1.30.0`; if beanie gets upgraded (e.g., `pip install beanie` without pin), downgrade with `pip install beanie==1.30.0`.
- If you must upgrade beanie, verify motor also exposes `append_metadata` on `AsyncIOMotorClient` before merging.
