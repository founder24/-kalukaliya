// Task #494: emit per-route static HTML stubs for non-data-driven
// pages so Lighthouse / Googlebot / AI crawlers see the route-specific
// <link rel="canonical"> in the served HTML instead of inheriting the
// homepage URL via the SPA fallback.
//
// These pages do NOT need an SSR snapshot in #root — they hydrate to
// real React content via the existing client bundle. We only rewrite
// the <head> (title, description, canonical, hreflang, og:url,
// twitter:title, twitter:description) so the static document Lighthouse
// inspects matches the actual route. The SPA shell, modulepreload, and
// asset hashes from dist/index.html are preserved unchanged.
//
// Routes covered:
//   /home               (LandingPage — public marketing landing)
//   /pricing
//   /login
//   /signup
//   /terms
//   /privacy
//   /about
//   /technology
//   /profile            (auth-gated shell — emit canonical + noindex)
//   /admin/login        (auth-gated shell — emit canonical + noindex)
//   /ahsec/hs-1st-year  (Task #2: AHSEC Class 11 index — high-value SEO target)
//   /ahsec/hs-2nd-year  (Task #2: AHSEC Class 12 index — high-value SEO target)
//   /notes/class-11     (Task #2: notes hub — long-tail "Class 11 notes" traffic)
//   /notes/class-12     (Task #2: notes hub — long-tail "Class 12 notes" traffic)
//   /notes/degree       (Task #2: notes hub — degree students)
//
// /chat and /library are prerendered with full SSR by their dedicated
// scripts; subject + chapter pages by scripts/prerender-routes.mjs.
//
// Task #499: even auth-gated shells (/profile, /admin/login) need a
// route-specific <link rel="canonical"> in the byte-zero HTML so the
// Lighthouse `canonical` SEO audit passes on every audited route.
// They keep `<meta name="robots" content="noindex, follow">` so search
// engines never index the shell, but the canonical still has to point
// to the correct URL (Lighthouse fails the audit when it is missing
// or inherits the homepage URL via the SPA fallback).

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const srcHtml = path.join(distDir, "index.html");

const SITE = "https://syrabit.ai";

const ROUTES = [
  {
    path: "/home",
    title:
      "Syrabit.ai — Educational Browser For Assam Board Students",
    description:
      "AI-powered educational browser for AHSEC, SEBA and Degree students in Assam. Browse syllabus content, get instant answers, and study smarter.",
    ogImageAlt: "Syrabit.ai — Educational Browser For Assam Board Students",
  },
  {
    path: "/pricing",
    title: "Pricing & Plans — Free, Starter & Pro | Syrabit.ai",
    description:
      "Compare Syrabit.ai plans for AHSEC and Degree students. Start free or upgrade to Starter (₹99) or Pro (₹999) for unlimited AI study help.",
    ogImageAlt: "Syrabit.ai Pricing & Plans — Free, Starter & Pro",
  },
  {
    path: "/login",
    title: "Log In to Syrabit.ai",
    description:
      "Sign in to Syrabit.ai to continue your AHSEC, SEBA or Degree exam preparation. Resume your study notes, MCQs, and AI chat history.",
    robots: "noindex, follow",
    ogImageAlt: "Log In to Syrabit.ai",
  },
  {
    path: "/signup",
    title: "Create Your Free Syrabit.ai Account",
    description:
      "Sign up free for Syrabit.ai — the AI-powered study platform built for Assam Board (AHSEC, SEBA) and Degree (B.Com, B.A, B.Sc) students.",
    ogImageAlt: "Create Your Free Syrabit.ai Account",
  },
  {
    path: "/terms",
    title: "Terms of Service | Syrabit.ai",
    description:
      "Terms and conditions for using Syrabit.ai — the AI-powered study platform for Assam Board and Degree students.",
    ogImageAlt: "Syrabit.ai Terms of Service",
  },
  {
    path: "/privacy",
    title: "Privacy Policy | Syrabit.ai",
    description:
      "How Syrabit.ai collects, uses and protects student data on our AI-powered exam preparation platform for AHSEC, SEBA and Degree students.",
    ogImageAlt: "Syrabit.ai Privacy Policy",
  },
  {
    path: "/about",
    title: "About Syrabit.ai — The Educational Browser For Assam",
    description:
      "Learn about Syrabit.ai, the AI-powered study platform built in Guwahati for AHSEC (Class 11-12), SEBA, and Degree students across Assam.",
    ogImageAlt: "About Syrabit.ai — The Educational Browser For Assam",
  },
  {
    // Stream landing pages — Task: stop serving the homepage HTML shell
    // for /ahsec, /seba, /degree. These are heavily-shared routes
    // (WhatsApp / Telegram link previews, Google search results) so the
    // byte-zero <head> must carry stream-specific title, description,
    // canonical, and og:image hooks. The React app still hydrates the
    // real content into #root after JS loads — we only own the <head>.
    path: "/ahsec",
    title:
      "AHSEC Class 11-12 Notes, MCQs & Solved PYQs — Syrabit.ai",
    description:
      "Free AHSEC Class 11 and Class 12 syllabus notes, MCQs, definitions and solved previous-year questions for Science, Commerce and Arts. AI study help in Assamese & English.",
    ogImageAlt: "AHSEC study materials — Syrabit.ai",
  },
  {
    path: "/seba",
    title:
      "SEBA Class 9-10 (HSLC) Notes, MCQs & Solved PYQs — Syrabit.ai",
    description:
      "Free SEBA Class 9 and Class 10 (HSLC) syllabus notes, chapter summaries, MCQs and solved previous-year questions. AI tutor in Assamese & English for every subject.",
    ogImageAlt: "SEBA study materials — Syrabit.ai",
  },
  {
    path: "/degree",
    title:
      "Gauhati University Degree Notes — B.A, B.Com, B.Sc | Syrabit.ai",
    description:
      "Free Gauhati University and Dibrugarh University degree syllabus notes, MCQs and exam-ready answers for B.A, B.Com and B.Sc (FYUGP) students across Assam.",
    ogImageAlt: "Degree study materials — Syrabit.ai",
  },
  {
    path: "/technology",
    title: "Technology Behind Syrabit.ai — RAG, AI Tutors & Speed",
    description:
      "How Syrabit.ai combines retrieval-augmented generation, AI tutors and Cloudflare's edge to deliver fast, syllabus-grounded answers for Assam students.",
    ogImageAlt: "Technology Behind Syrabit.ai — RAG, AI Tutors & Speed",
  },
  {
    // Task #499: auth-gated user shell — must ship its own canonical
    // even though it's noindex,follow, so the Lighthouse canonical
    // SEO audit passes (today it fails because the SPA fallback for
    // /profile carries no canonical at byte zero).
    path: "/profile",
    title: "Your Profile — Syrabit.ai",
    description:
      "Manage your Syrabit.ai account, study history, and AHSEC, SEBA or Degree exam preparation preferences.",
    robots: "noindex, follow",
    ogImageAlt: "Your Profile — Syrabit.ai",
  },
  {
    // Task #499: admin login is also noindex but still needs a
    // route-specific canonical at byte zero so the SEO audit doesn't
    // fail on this URL.
    path: "/admin/login",
    title: "Admin Login | Syrabit.ai",
    description:
      "Internal Syrabit.ai administrator sign-in. Not for student accounts — students log in at /login instead.",
    robots: "noindex, follow",
    ogImageAlt: "Admin Login — Syrabit.ai",
  },
  {
    // Task #2 (SEO Quick Wins): AHSEC class-level index pages are high-value
    // SEO targets (e.g. "AHSEC Class 11 notes" → /ahsec/hs-1st-year) and
    // are shared heavily on WhatsApp / Telegram. These routes get byte-zero
    // head metadata so Googlebot sees keyword-rich titles without waiting
    // for React to hydrate.
    path: "/ahsec/hs-1st-year",
    title: "AHSEC HS 1st Year (Class 11) Notes, MCQs & PYQs — Syrabit.ai",
    description:
      "Free AHSEC Class 11 (HS 1st Year) notes, MCQs, definitions and solved previous-year questions for all subjects — Science, Commerce and Arts. AI study help in Assamese & English.",
    ogImageAlt: "AHSEC HS 1st Year (Class 11) study materials — Syrabit.ai",
  },
  {
    path: "/ahsec/hs-2nd-year",
    title: "AHSEC HS 2nd Year (Class 12) Notes, MCQs & PYQs — Syrabit.ai",
    description:
      "Free AHSEC Class 12 (HS 2nd Year) notes, MCQs, definitions and solved previous-year questions for all subjects — Science, Commerce and Arts. AI study help in Assamese & English.",
    ogImageAlt: "AHSEC HS 2nd Year (Class 12) study materials — Syrabit.ai",
  },
  {
    // Task #2 (SEO Quick Wins): /notes/* hub pages drive long-tail search
    // traffic for "Class 11 notes", "Class 12 notes", "Degree notes" queries
    // and appear in WhatsApp link previews shared between students.
    path: "/notes/class-11",
    title: "Class 11 Notes, MCQs & PYQs for Assam Board (AHSEC) — Syrabit.ai",
    description:
      "Browse free AHSEC Class 11 (HS 1st Year) chapter notes, MCQs and solved PYQs for every subject. AI-powered study help in Assamese & English.",
    ogImageAlt: "Class 11 study notes — Syrabit.ai",
  },
  {
    path: "/notes/class-12",
    title: "Class 12 Notes, MCQs & PYQs for Assam Board (AHSEC) — Syrabit.ai",
    description:
      "Browse free AHSEC Class 12 (HS 2nd Year) chapter notes, MCQs and solved PYQs for every subject. AI-powered study help in Assamese & English.",
    ogImageAlt: "Class 12 study notes — Syrabit.ai",
  },
  {
    path: "/notes/degree",
    title: "Degree Notes — B.A, B.Com, B.Sc (GU / DU) | Syrabit.ai",
    description:
      "Free Gauhati University and Dibrugarh University degree chapter notes, MCQs and exam-ready answers for B.A, B.Com, and B.Sc (FYUGP) students.",
    ogImageAlt: "Degree study notes — Syrabit.ai",
  },
];

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function rewriteHead(html, { title, description, canonical, robots, ogImageAlt }) {
  html = html.replace(
    /<title>[^<]*<\/title>/,
    `<title>${escapeHtml(title)}</title>`,
  );
  html = html.replace(
    /<meta name="description" content="[^"]*"\s*\/?>(\n)?/,
    `<meta name="description" content="${escapeHtml(description)}" />\n    `,
  );

  // Insert canonical + hreflang. Swap if a placeholder exists (legacy
  // build), else inject before </head> so Lighthouse always sees one
  // canonical tag pointing to the real route.
  if (/<link rel="canonical" href="[^"]*"\s*\/?>(\n)?/.test(html)) {
    html = html.replace(
      /<link rel="canonical" href="[^"]*"\s*\/?>(\n)?/,
      `<link rel="canonical" href="${canonical}" />\n    `,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <link rel="canonical" href="${canonical}" />\n` +
      `    <link rel="alternate" hreflang="en-IN" href="${canonical}" />\n  </head>`,
    );
  }

  // og:url + matching titles/descriptions + og:image:alt
  html = html.replace(
    /<meta property="og:url" content="[^"]*"\s*\/?>/,
    `<meta property="og:url" content="${canonical}" />`,
  );
  html = html.replace(
    /<meta property="og:title" content="[^"]*"\s*\/?>/,
    `<meta property="og:title" content="${escapeHtml(title)}" />`,
  );
  html = html.replace(
    /<meta property="og:description" content="[^"]*"\s*\/?>/,
    `<meta property="og:description" content="${escapeHtml(description)}" />`,
  );
  if (ogImageAlt) {
    html = html.replace(
      /<meta property="og:image:alt" content="[^"]*"\s*\/?>/,
      `<meta property="og:image:alt" content="${escapeHtml(ogImageAlt)}" />`,
    );
  }
  html = html.replace(
    /<meta name="twitter:title" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:title" content="${escapeHtml(title)}" />`,
  );
  html = html.replace(
    /<meta name="twitter:description" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:description" content="${escapeHtml(description)}" />`,
  );
  // Task #38: inject twitter:image and twitter:image:alt so the edge-proxy
  // HTMLRewriter has a tag to rewrite and X/Twitter link previews use the
  // route-specific banner rather than falling back to nothing.
  if (/<meta name="twitter:image" content="[^"]*"\s*\/?>/.test(html)) {
    html = html.replace(
      /<meta name="twitter:image" content="[^"]*"\s*\/?>/,
      `<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />\n  </head>`,
    );
  }
  if (ogImageAlt) {
    if (/<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/.test(html)) {
      html = html.replace(
        /<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/,
        `<meta name="twitter:image:alt" content="${escapeHtml(ogImageAlt)}" />`,
      );
    } else {
      html = html.replace(
        /<\/head>/,
        `    <meta name="twitter:image:alt" content="${escapeHtml(ogImageAlt)}" />\n  </head>`,
      );
    }
  }

  // Optional per-route robots override (e.g. /login is noindex,follow).
  if (robots) {
    if (/<meta name="robots" content="[^"]*"\s*\/?>/.test(html)) {
      html = html.replace(
        /<meta name="robots" content="[^"]*"\s*\/?>/,
        `<meta name="robots" content="${escapeHtml(robots)}" />`,
      );
    } else {
      html = html.replace(
        /<\/head>/,
        `    <meta name="robots" content="${escapeHtml(robots)}" />\n  </head>`,
      );
    }
  }

  return html;
}

function main() {
  if (!fs.existsSync(srcHtml)) {
    console.warn(
      `[prerender-static-routes] dist/index.html not found at ${srcHtml}; skipping`,
    );
    return;
  }

  const baseHtml = fs.readFileSync(srcHtml, "utf-8");
  let written = 0;
  const summary = [];

  for (const route of ROUTES) {
    const canonical = `${SITE}${route.path}`;
    const outDir = path.join(distDir, route.path.replace(/^\//, ""));
    const outFile = path.join(outDir, "index.html");

    // Don't overwrite a real SSR'd prerender if one already exists for
    // this path (e.g. some future task adds full SSR for /pricing).
    if (fs.existsSync(outFile)) {
      const existing = fs.readFileSync(outFile, "utf-8");
      if (/data-hydrate="[a-z]+"/.test(existing)) {
        console.log(
          `[prerender-static-routes] skipping ${route.path} — full SSR snapshot already present`,
        );
        continue;
      }
    }

    const html = rewriteHead(baseHtml, {
      title: route.title,
      description: route.description,
      canonical,
      robots: route.robots,
      ogImageAlt: route.ogImageAlt,
    });

    // Hard assertion: exactly one <link rel="canonical"> with the
    // expected href. Catches accidental regressions where a stray
    // placeholder canonical leaks into the static template again.
    const canonicalTags =
      html.match(/<link\s+rel="canonical"\s+href="[^"]*"[^>]*>/g) || [];
    if (canonicalTags.length !== 1) {
      throw new Error(
        `[prerender-static-routes] ${route.path}: expected exactly 1 canonical tag, found ${canonicalTags.length}`,
      );
    }
    if (!canonicalTags[0].includes(`href="${canonical}"`)) {
      throw new Error(
        `[prerender-static-routes] ${route.path}: canonical points to wrong URL — ${canonicalTags[0]}`,
      );
    }
    // Hard assertion: og:image:alt must be present and non-empty so social
    // crawlers always get a descriptive alt string on first byte.
    if (!/<meta property="og:image:alt" content="[^"]+"/.test(html)) {
      throw new Error(
        `[prerender-static-routes] ${route.path}: og:image:alt missing or empty in prerendered HTML`,
      );
    }
    // Task #38: twitter:image and twitter:image:alt must be present so the
    // edge-proxy HTMLRewriter can rewrite them and X/Twitter link previews
    // show the route-specific banner rather than falling back to nothing.
    if (!/<meta name="twitter:image" content="[^"]+"/.test(html)) {
      throw new Error(
        `[prerender-static-routes] ${route.path}: twitter:image missing or empty in prerendered HTML`,
      );
    }
    if (!/<meta name="twitter:image:alt" content="[^"]+"/.test(html)) {
      throw new Error(
        `[prerender-static-routes] ${route.path}: twitter:image:alt missing or empty in prerendered HTML`,
      );
    }

    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(outFile, html);
    written++;
    summary.push({ path: route.path, canonical });
    console.log(
      `[prerender-static-routes] wrote ${path.relative(distDir, outFile)} ` +
        `(canonical=${canonical}${route.robots ? `, robots=${route.robots}` : ""})`,
    );
  }

  console.log(
    `[prerender-static-routes] done — ${written}/${ROUTES.length} static-route stubs written`,
  );

  // Persist a tiny manifest so verify-all.mjs can iterate over
  // the exact set of routes this script claims to have produced.
  fs.writeFileSync(
    path.join(distDir, "prerender-static-manifest.json"),
    JSON.stringify(
      { generated_at: new Date().toISOString(), routes: summary },
      null,
      2,
    ),
  );
}

main();
