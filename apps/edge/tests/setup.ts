/**
 * Global test setup: mock Cloudflare Worker globals that are not available in Node.
 */
import { vi } from 'vitest';

// Mock the CF Cache API global
const mockCache = {
  match: vi.fn(async () => undefined),
  put: vi.fn(async () => {}),
  delete: vi.fn(async () => false),
};

const mockCaches = {
  default: mockCache,
  open: vi.fn(async () => mockCache),
};

vi.stubGlobal('caches', mockCaches);
