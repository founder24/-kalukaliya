---
name: AHSEC importer workflow runtime
description: Runtime requirements for the local AHSEC D1 dry-run workflow
---

The AHSEC D1 importer workflow runs under a PEP 668 externally managed Python and must use the explicit break-system-packages install flag; the importer uses pypdfium2 for PDF extraction.

**Why:** The workflow reached catalogue extraction but failed before processing PDFs when pip was blocked by PEP 668, then failed again when the dependency was absent from the backend requirements.

**How to apply:** Keep the workflow install command and locked backend requirements aligned whenever the importer is run from the Replit workflow.

The workspace user-site can retain packages that were removed from the backend requirements; a local license scan may report these stale distributions even when they are absent from the lock.

**Why:** The configured workflow installs requirements with user-scoped pip, which does not uninstall packages omitted from a later requirements file.

**How to apply:** Audit the locked dependency set in a clean isolated environment with user-site and inherited `PYTHONPATH` disabled. Do not remove stale packages from the shared user-site as part of a project dependency migration.