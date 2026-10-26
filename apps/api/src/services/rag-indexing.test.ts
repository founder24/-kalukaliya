import { describe, expect, it } from 'vitest';
import { batchVectorIds } from './rag-indexing';

describe('batchVectorIds', () => {
  it('keeps every Vectorize delete request within the 100-ID limit', () => {
    const ids = Array.from({ length: 1_004 }, (_, index) => `vector-${index}`);
    const batches = batchVectorIds(ids);

    expect(batches.map(batch => batch.length)).toEqual([100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 4]);
    expect(batches.flat()).toEqual(ids);
  });

  it('does not create an empty request when there are no IDs', () => {
    expect(batchVectorIds([])).toEqual([]);
  });
});
