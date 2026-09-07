import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PageMeta from "./PageMeta";

afterEach(() => {
  cleanup();
  document.head.innerHTML = "";
});

describe("PageMeta route transitions", () => {
  it("removes stale article and hreflang metadata when history restores a website route", async () => {
    const articleUrl = "https://syrabit.ai/notes/first";
    const homeUrl = "https://syrabit.ai/library";
    const { rerender } = render(
      <PageMeta
        title="First note"
        description="A sufficiently descriptive article metadata test."
        url={articleUrl}
        type="article"
        section="Physics"
        tags={["mechanics"]}
        publishedTime="2026-01-01T00:00:00Z"
        modifiedTime="2026-01-02T00:00:00Z"
        hasAssamese
      />,
    );

    await waitFor(() => {
      expect(document.title).toBe("First note | Syrabit.ai");
      expect(document.head.querySelector('meta[property="article:section"]')).not.toBeNull();
      expect(document.head.querySelector('link[hreflang="as"]')).not.toBeNull();
    });

    rerender(
      <PageMeta
        title="Study Library"
        description="Browse the complete Assam Board study library."
        url={homeUrl}
      />,
    );

    await waitFor(() => {
      expect(document.title).toBe("Study Library | Syrabit.ai");
      expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute("href")).toBe(homeUrl);
      expect(document.head.querySelector('meta[property="article:section"]')).toBeNull();
      expect(document.head.querySelector('meta[property="article:published_time"]')).toBeNull();
      expect(document.head.querySelector('meta[property="article:tag"]')).toBeNull();
      expect(document.head.querySelector('link[hreflang="as"]')).toBeNull();
      expect(document.head.querySelector('link[hreflang="x-default"]')).toBeNull();
      expect(document.head.querySelector('link[hreflang="en-IN"]')?.getAttribute("href")).toBe(homeUrl);
    });

    rerender(<PageMeta title="Fallback route" />);
    await waitFor(() => {
      expect(document.title).toBe("Fallback route | Syrabit.ai");
      expect(document.head.querySelector('meta[name="description"]')).toBeNull();
      expect(document.head.querySelector('meta[property="og:description"]')).toBeNull();
      expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
    });
  });
});