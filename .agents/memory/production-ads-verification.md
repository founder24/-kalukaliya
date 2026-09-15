---
name: Production AdSense verification
description: Evidence required to distinguish a working Google loader from a correctly configured production AdSense release
---

Production ad verification must check the deployed bundle and browser DOM independently. A page can load `adsbygoogle.js` while all manual `<ins>` units remain disabled because the build received no numeric `VITE_ADS_ADSENSE_*_SLOT` values. The same pass must record supplied iframe fills separately from no-fill outcomes and fail on stale hashed-asset 404s or unexpected page errors.

**Why:** A production build can be healthy enough to load the Google publisher script while the build-time Pages configuration loader was absent or skipped, making the user-visible manual placements silently disappear.

**How to apply:** For opted-in Notes, Q&A, and PYQ routes, assert the loader, numeric `data-ad-slot` metadata, and any iframe fill. Repeat with the opt-out state and assert no loader, ad container, reserved height, or iframe. Use a fresh browser context with service workers blocked as an additional control when diagnosing cache-related failures.

Deterministic release coverage must run against the production-shaped client build after all slot IDs are loaded, with Google’s loader and iframe outcomes controlled in the browser; keep the live route smoke separate.

**Why:** Live Google fills are nondeterministic, while a fixture can prove numeric placement metadata and distinguish a valid no-fill from a broken loader. Mixing the two makes release failures ambiguous.

**How to apply:** Use unique numeric fixture IDs, assert fluid no-fill containers have zero layout height, and use a service-worker-blocked fresh context for the real production route so stale hashed assets remain visible as release failures.