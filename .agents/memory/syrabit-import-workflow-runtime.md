---
name: AHSEC importer workflow runtime
description: Runtime requirements for the local AHSEC D1 dry-run workflow
---

The AHSEC D1 importer workflow runs under a PEP 668 externally managed Python and must use the explicit break-system-packages install flag; the importer also requires PyMuPDF because PDF extraction imports the `fitz` compatibility module.

**Why:** The workflow reached catalogue extraction but failed before processing PDFs when pip was blocked by PEP 668, then failed again when the dependency was absent from the backend requirements.

**How to apply:** Keep the workflow install command and locked backend requirements aligned whenever the importer is run from the Replit workflow.

For backend tests, when the workspace Python environment has no pytest, use uv with `--no-project` and both backend requirement files. Project-aware uv and the Replit package-install helper may try to write packages into the read-only Nix store; user-scoped pip can install into the workspace's writable `.pythonlibs` site.

**Why:** The Nix system Python site-packages is immutable, so project-aware package installation can fail with a permissions error even after dependencies resolve. A user-scoped install or isolated no-project runner avoids changing production dependencies.

**How to apply:** Prefer isolated focused runs with `cd apps/backend && PYTHONPATH=. uv run --no-project --with-requirements requirements.txt --with-requirements requirements-dev.in pytest ...`. If installing the dev lock into the workspace, use `python3 -m pip install --user --break-system-packages --requirement requirements-dev.txt`; never add pytest tools to production `requirements.in` or `requirements.txt`.