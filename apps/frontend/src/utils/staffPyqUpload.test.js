import { describe, expect, it, vi } from 'vitest';
import { uploadPyqFilesInOrder } from './staffPyqUpload';

describe('uploadPyqFilesInOrder', () => {
  it('awaits each page and preserves selection order', async () => {
    const items = [{ file: { name: 'page-1.jpg' } }, { file: { name: 'page-2.jpg' } }];
    const calls = [];
    const onPageUploaded = vi.fn();
    const uploadFile = vi.fn(async (file) => {
      calls.push(`start:${file.name}`);
      await Promise.resolve();
      calls.push(`finish:${file.name}`);
      return { pyq_papers: [{ id: file.name, url: `/files/${file.name}` }] };
    });

    const result = await uploadPyqFilesInOrder({ items, uploadFile, onPageUploaded });

    expect(calls).toEqual([
      'start:page-1.jpg',
      'finish:page-1.jpg',
      'start:page-2.jpg',
      'finish:page-2.jpg',
    ]);
    expect(uploadFile.mock.calls.map(([file]) => file.name)).toEqual(['page-1.jpg', 'page-2.jpg']);
    expect(onPageUploaded).toHaveBeenCalledTimes(2);
    expect(result).toMatchObject({ uploadedCount: 2, failed: [] });
  });

  it('continues after one page fails and reports the partial result', async () => {
    const items = [{ file: { name: 'bad.jpg' } }, { file: { name: 'good.jpg' } }];
    const statuses = [];
    const uploadFile = vi.fn(async (file) => {
      if (file.name === 'bad.jpg') throw new Error('bad image');
      return { pyq_papers: [{ id: 'good', url: '/files/good.jpg' }] };
    });

    const result = await uploadPyqFilesInOrder({
      items,
      uploadFile,
      onStatus: (_item, status, error) => statuses.push([_item.file.name, status, error]),
    });

    expect(statuses).toEqual([
      ['bad.jpg', 'uploading', undefined],
      ['bad.jpg', 'failed', 'bad image'],
      ['good.jpg', 'uploading', undefined],
      ['good.jpg', 'uploaded', undefined],
    ]);
    expect(result.uploadedCount).toBe(1);
    expect(result.failed[0].message).toBe('bad image');
  });
});