---
name: Staff chapter PYQ uploads
description: The live pagewise image upload contract for staff chapter editors
---

The staff chapter editor's pagewise PYQ image flow must use the Worker-native chapter routes: load chapter metadata, append each image through the chapter `pyq-papers` endpoint in selection order, and delete through its matching page route. Legacy `/admin/pyq/*` endpoints are not mounted by the current Worker.

**Why:** The legacy editor could still display a PYQ panel while every load/upload/delete request failed after the Worker cutover, making the pagewise option appear missing or empty.

**How to apply:** When changing the admin editor or PYQ page UI, preserve one-record-per-image ordering and use the staff chapter routes with the existing staff auth contract. Keep OCR out of this page-image path.

Browser uploads must let the HTTP client generate the `multipart/form-data` boundary; do not set that content type manually when posting `FormData`.

**Why:** The Worker parser needs the boundary parameter to identify the file part. A manually supplied header can omit it, so the UI may report failed uploads even though the queue and route are correct.

**How to apply:** Append the file to `FormData` and pass it directly to the client. Test the outgoing request header for a `boundary=` value in browser coverage.