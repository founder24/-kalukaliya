import { describe, it, expect, beforeEach } from "vitest";
import worker from "../src/index";
import { resetD1SyncedCache } from "../src/d1-queries";

type Row = Record<string, unknown>;

function makeStmt(sql: string) {
  const lower = sql.toLowerCase();
  return {
    bind() {
      return this;
    },
    async first<T = Row>(): Promise<T | null> {
      if (lower.includes("from sync_meta")) {
        return { value: "2026-04-22T00:00:00Z" } as unknown as T;
      }
      return null;
    },
    async all<T = Row>(): Promise<{ results: T[] }> {
      if (lower.includes("from seo_pages")) {
        return { results: [] };
      }
      return { results: [] };
    },
    async run() {
      return { success: true };
    },
  };
}

const fakeDb = {
  prepare(sql: string) {
    return makeStmt(sql);
  },
  async batch(stmts: Array<{ bind: () => unknown; first: () => Promise<unknown> }>) {
    return Promise.all(stmts.map(() => ({ results: [] as Row[] })));
  },
  async exec() {
    return { count: 0, duration: 0 };
  },
} as unknown as D1Database;

const env = {
  BACKEND_URL: "https://example.invalid",
  RATE_LIMIT: undefined as unknown as KVNamespace,
  CONTENT_DB: fakeDb,
  D1_SYNC_SECRET: "x",
  JWT_SECRET: "test-jwt-secret",
} as unknown as Parameters<typeof worker.fetch>[1];

const ctx = {
  waitUntil: () => undefined,
  passThroughOnException: () => undefined,
} as unknown as ExecutionContext;

describe("/sitemap.xml alias (Task #672 guard)", () => {
  beforeEach(() => {
    resetD1SyncedCache();
  });

  for (const method of ["GET", "HEAD"] as const) {
    it(`responds with the D1 sitemap index on ${method}`, async () => {
      const req = new Request("https://syrabit.ai/sitemap.xml", { method });
      const res = await worker.fetch(req, env, ctx);

      expect(res.status).toBe(200);

      const ct = res.headers.get("content-type") || "";
      expect(ct.toLowerCase().startsWith("application/xml")).toBe(true);

      const body = await res.text();
      if (method === "GET") {
        expect(body.startsWith("<?xml")).toBe(true);
        expect(body).toContain("<sitemapindex");
      }
      // HEAD intentionally validates status + content-type only: per
      // RFC 7231 section 4.3.2 a HEAD response carries no message body,
      // so asserting on body shape would be testing the runtime, not
      // the alias behavior we care about for Task #672/#685.
    });
  }
});
