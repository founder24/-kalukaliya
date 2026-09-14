---
name: AHSEC importer workflow runtime
description: Runtime requirements for the local AHSEC D1 dry-run workflow
---

The AHSEC D1 importer workflow runs under a PEP 668 externally managed Python and must use the explicit break-system-packages install flag; the importer also requires PyMuPDF because PDF extraction imports the `fitz` compatibility module.

**Why:** The workflow reached catalogue extraction but failed before processing PDFs when pip was blocked by PEP 668, then failed again when the dependency was absent from the backend requirements.

**How to apply:** Keep the workflow install command and locked backend requirements aligned whenever the importer is run from the Replit workflow.