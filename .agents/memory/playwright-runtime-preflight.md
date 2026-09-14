---
name: Playwright runtime preflight
description: Chromium release checks must validate the headless-shell executable, not only the regular browser archive
---

Playwright headless mode launches the separately downloaded `chromium_headless_shell` binary. A regular Chromium executable can pass dependency inspection while the headless shell still fails to load a shared library; validate the headless shell directly and report the library name before hydration starts.

**Why:** On the Nix-based development runner, `ldd` and the regular browser archive appeared healthy while the real headless process failed on `libglib-2.0.so.0`; the release hydration check otherwise reported this as a late browser failure.

**How to apply:** Keep OS-library provisioning in the release workflow before browser checks, and keep the hydration verifier focused on application/hydration findings. Use an executable-level headless-shell preflight for the environment boundary.