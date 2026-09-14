/**
 * Staff chapter writes intentionally exclude server/admin-owned fields.
 * PYQ assets are changed through their dedicated upload route, not through
 * the chapter metadata PATCH contract.
 */
export function buildStaffChapterPatchPayload(form = {}) {
  const {
    published_topics: _publishedTopics,
    pyq_pdf_url: _pyqPdfUrl,
    ...payload
  } = form;
  return payload;
}

export function normalizeStaffChapterCreateStatus(status, canPublish) {
  return canPublish && status === 'published' ? 'published' : 'draft';
}