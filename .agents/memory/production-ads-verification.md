---
name: Production AdSense verification
description: Evidence required to distinguish a working Google loader from a correctly configured production AdSense release
---

Production ad verification must check the deployed bundle and browser DOM independently. A page can load `adsbygoogle.js` while all manual `<ins>` units remain disabled because the build received no numeric `VITE_ADS_ADSENSE_*_SLOT` values. The same pass must record supplied iframe fills separately from no-fill outcomes and fail on stale hashed-asset 404s or unexpected page errors.

**Why:** A production build can be healthy enough to load the Google publisher script while the build-time Pages configuration loader was absent or skipped, making the user-visible manual placements silently disappear.

**How to apply:** For opted-in Notes, Q&A, and PYQ routes, assert the loader, numeric `data-ad-slot` metadata, and any iframe fill. Repeat with the opt-out state and assert no loader, ad container, reserved height, or iframe. Use a fresh browser context with service workers blocked as an additional control when diagnosing cache-related failures.