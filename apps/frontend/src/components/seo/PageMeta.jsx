// PageMeta — manages <head> tags via useEffect, renders nothing.
//
// Why: React 19's "native" <title>/<meta>/<link> hoisting renders the tags
// inline in body during SSR but hoists them away (or never emits them in
// body) on the client first render. That difference triggers React error
// #418 hydration mismatch on every prerendered page. The same problem
// existed earlier with react-helmet-async.
//
// For prerendered routes, scripts/prerender-routes.mjs already injects
// <title>, <meta name="description">, <link rel="canonical">, og:*,
// twitter:*, and hreflang alternates into <head>. So this component only
// needs to:
//   - keep <head> in sync during SPA navigation (route changes after
//     hydration), where the prerender pipeline is not in play
//   - inject JSON-LD <script> tags (also under data-pm cleanup)
//
// By returning null, SSR body and client first-render body are identical
// (both empty for this component) → no hydration mismatch.
import { useEffect, useMemo } from "react";
import { buildSchemaForPageType, dedupeGraphTypes } from "@/lib/jsonld";

const SITE_NAME = "Syrabit.ai";
const ABS_DEFAULT_IMG = "https://syrabit.ai/opengraph.jpg";
const MARKER_ATTR = "data-pm";

function setMetaByName(name, content) {
  if (content == null || content === "") return;
  let el = document.head.querySelector(`meta[name="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute("name", name);
    el.setAttribute(MARKER_ATTR, "1");
    document.head.appendChild(el);
  }
  el.setAttribute("content", String(content));
}

function setMetaByProperty(property, content) {
  if (content == null || content === "") return;
  let el = document.head.querySelector(`meta[property="${property}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute("property", property);
    el.setAttribute(MARKER_ATTR, "1");
    document.head.appendChild(el);
  }
  el.setAttribute("content", String(content));
}

function setLink(rel, href, extraAttrs = {}) {
  if (!href) return;
  // For rel="alternate" with hreflang, multiple links are valid; key by hreflang.
  const hreflang = extraAttrs.hreflang;
  const selector = hreflang
    ? `link[rel="${rel}"][hreflang="${hreflang}"]`
    : `link[rel="${rel}"]`;
  let el = document.head.querySelector(selector);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    el.setAttribute(MARKER_ATTR, "1");
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
  for (const [k, v] of Object.entries(extraAttrs)) el.setAttribute(k, v);
}

function syncJsonLd(blocks) {
  // Remove our previously-managed JSON-LD blocks, then re-add. Keeps any
  // JSON-LD injected by the prerender pipeline (no data-pm attr) intact.
  document.head
    .querySelectorAll(`script[type="application/ld+json"][${MARKER_ATTR}]`)
    .forEach((el) => el.remove());
  for (const block of blocks) {
    const s = document.createElement("script");
    s.type = "application/ld+json";
    s.setAttribute(MARKER_ATTR, "1");
    s.textContent = JSON.stringify(block);
    document.head.appendChild(s);
  }
}

export default function PageMeta({
  title,
  description,
  url,
  image = ABS_DEFAULT_IMG,
  keywords,
  type = "website",
  section,
  tags,
  publishedTime,
  modifiedTime,
  jsonLd,
  pageType,
  pageData,
  hasAssamese = false,
}) {
  // Stable, content-derived signatures so the JSON-LD effect re-runs
  // only when the underlying schema content changes, not on every
  // render that hands us a referentially-new pageData/jsonLd object.
  // We hash the JSON-stringified inputs (cheap, short — these payloads
  // are small) instead of relying on object identity.
  const pageDataSig = useMemo(() => {
    if (!pageData) return "";
    try { return JSON.stringify(pageData); } catch { return ""; }
  }, [pageData]);
  const jsonLdSig = useMemo(() => {
    if (!jsonLd) return "";
    try { return JSON.stringify(jsonLd); } catch { return ""; }
  }, [jsonLd]);

  useEffect(() => {
    if (typeof document === "undefined") return;

    const finalTitle = title ? `${title} | ${SITE_NAME}` : SITE_NAME;
    const absImage = image && image.startsWith("http")
      ? image
      : `https://syrabit.ai${image || ""}`;

    document.title = finalTitle;

    setMetaByName("description", description);
    if (keywords) setMetaByName("keywords", keywords);

    setLink("canonical", url);

    setMetaByProperty("og:site_name", SITE_NAME);
    setMetaByProperty("og:locale", "en_IN");
    setMetaByProperty("og:title", title);
    setMetaByProperty("og:description", description);
    setMetaByProperty("og:type", type);
    setMetaByProperty("og:url", url);
    setMetaByProperty("og:image", absImage);
    setMetaByProperty("og:image:width", "1200");
    setMetaByProperty("og:image:height", "630");

    if (type === "article") {
      if (section) setMetaByProperty("article:section", section);
      if (publishedTime) setMetaByProperty("article:published_time", publishedTime);
      if (modifiedTime) setMetaByProperty("article:modified_time", modifiedTime);
      // article:tag — multi-valued. Remove old managed ones then re-add.
      document.head
        .querySelectorAll(`meta[property="article:tag"][${MARKER_ATTR}]`)
        .forEach((el) => el.remove());
      if (Array.isArray(tags)) {
        for (const t of tags) {
          const m = document.createElement("meta");
          m.setAttribute("property", "article:tag");
          m.setAttribute("content", String(t));
          m.setAttribute(MARKER_ATTR, "1");
          document.head.appendChild(m);
        }
      }
    }

    setMetaByName("twitter:card", "summary_large_image");
    setMetaByName("twitter:site", "@SyrabitAI");
    setMetaByName("twitter:title", title);
    setMetaByName("twitter:description", description);
    setMetaByName("twitter:image", absImage);

    setMetaByName("geo.region", "IN-AS");
    setMetaByName("geo.placename", "Assam, India");
    setMetaByName("geo.position", "26.2006;92.9376");
    setMetaByName("ICBM", "26.2006, 92.9376");
    setMetaByName(
      "robots",
      "index, follow, max-snippet:-1, max-image-preview:large",
    );

    // hreflang alternates
    if (hasAssamese && url) {
      const asUrl = url.includes("?") ? `${url}&lang=as` : `${url}?lang=as`;
      setLink("alternate", url, { hreflang: "en" });
      setLink("alternate", asUrl, { hreflang: "as" });
      setLink("alternate", url, { hreflang: "x-default" });
    } else if (url) {
      setLink("alternate", url, { hreflang: "en-IN" });
    }

    // JSON-LD blocks
    const externalLd = jsonLd
      ? Array.isArray(jsonLd) ? jsonLd : [jsonLd]
      : [];
    const rawTyped = pageType
      ? buildSchemaForPageType(pageType, { url, ...(pageData || {}) })
      : null;
    const typedSchema = rawTyped ? dedupeGraphTypes(rawTyped, externalLd) : null;
    const allLd = [
      ...(typedSchema && Array.isArray(typedSchema["@graph"]) && typedSchema["@graph"].length
        ? [typedSchema]
        : []),
      ...externalLd,
    ];
    syncJsonLd(allLd);
  }, [
    title, description, url, image, keywords, type, section,
    publishedTime, modifiedTime, hasAssamese, pageType,
    // Tags / jsonLd / pageData are intentionally NOT raw deps — each
    // render produces a referentially-new array/object that would
    // thrash the effect. Instead we feed in `pageDataSig` and
    // `jsonLdSig`, content-derived signatures that change only when
    // schema-relevant data changes (e.g. `pageData.data.faq_entries`
    // arriving from the async FAQ-JSON-LD fetch on chapter pages —
    // P0 #1 of the AI-visibility plan).
    pageDataSig,
    jsonLdSig,
  ]);

  return null;
}
