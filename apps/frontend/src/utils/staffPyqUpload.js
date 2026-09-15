/**
 * Upload selected PYQ page files one at a time.
 *
 * The API appends one page per request, so awaiting each request is what keeps
 * the user's file-selection order equal to the public page order.
 */
export async function uploadPyqFilesInOrder({
  items,
  uploadFile,
  onPageUploaded = () => {},
  onStatus = () => {},
}) {
  let latestPapers;
  let uploadedCount = 0;
  const failed = [];

  for (const item of items) {
    onStatus(item, 'uploading');
    try {
      const response = await uploadFile(item.file);
      if (Array.isArray(response?.pyq_papers)) {
        latestPapers = response.pyq_papers;
        onPageUploaded(latestPapers, response);
      }
      uploadedCount += 1;
      onStatus(item, 'uploaded');
    } catch (error) {
      const message = error?.response?.data?.detail || error?.message || 'Upload failed';
      failed.push({ item, error, message });
      onStatus(item, 'failed', message);
    }
  }

  return { uploadedCount, failed, latestPapers };
}