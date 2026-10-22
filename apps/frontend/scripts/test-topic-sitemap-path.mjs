import assert from "node:assert/strict";
import { decodeHtmlEntities } from "./_html-text.mjs";
import { canonicalTopicSitemapPath } from "./_topic-sitemap-path.mjs";

const assamesePath = "/as/ahsec/class-12/physics/newtons-laws/topic/প্ৰথম-নিয়ম";
const encodedAssamesePath =
  `/as/ahsec/class-12/physics/newtons-laws/topic/${encodeURIComponent("প্ৰথম-নিয়ম")}`;
assert.equal(canonicalTopicSitemapPath(assamesePath), encodedAssamesePath);
assert.equal(canonicalTopicSitemapPath(encodedAssamesePath), encodedAssamesePath);
assert.equal(
  canonicalTopicSitemapPath("/ahsec/class-12/physics/newtons-laws/topic/ohm-law"),
  "/ahsec/class-12/physics/newtons-laws/topic/ohm-law",
);
assert.equal(
  decodeHtmlEntities("Follet&#39;s / Follet&#x27;s"),
  "Follet's / Follet's",
);

console.log("[topic-sitemap] encoded routes and HTML title entities normalize correctly");
