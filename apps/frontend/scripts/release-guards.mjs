// Shared, side-effect-free guards for curriculum release artifacts.
//
// Keep these checks independent from the network, filesystem, and browser so
// CI can exercise the production contract with small in-memory fixtures.

export function isStrictCurriculumBuild(env = process.env) {
  return (
    env.CLOUDFLARE_RELEASE_BUILD === "true" ||
    (env.NODE_ENV === "production" &&
      env.ALLOW_INCOMPLETE_CURRICULUM_BUILD !== "true")
  );
}

export function hasNonEmptyLibraryBundle(bundle) {
  return (
    Boolean(bundle) &&
    Array.isArray(bundle.subjects) &&
    bundle.subjects.length > 0
  );
}

export function validateLibrarySnapshot(rel, body, { strict = false } = {}) {
  const failures = [];
  const warnings = [];
  const report = strict ? failures : warnings;
  const bundleMarker = "window.__LIBRARY_BUNDLE__=";
  const bundleStart = body.indexOf(bundleMarker);
  const bundleEnd =
    bundleStart === -1
      ? -1
      : body.indexOf(";window.__SSR_QUERIES__", bundleStart);

  if (bundleStart === -1 || bundleEnd === -1) {
    report.push(`${rel}: missing inlined library bundle payload`);
    return { failures, warnings };
  }

  try {
    const bundle = JSON.parse(
      body.slice(bundleStart + bundleMarker.length, bundleEnd),
    );
    if (!hasNonEmptyLibraryBundle(bundle)) {
      report.push(`${rel}: inlined library bundle contains zero subjects`);
    }
  } catch (err) {
    report.push(
      `${rel}: inlined library bundle is not valid JSON (${err.message})`,
    );
  }

  return { failures, warnings };
}

export function validatePrerenderManifest(
  manifest,
  { strict = false } = {},
) {
  const failures = [];
  const warnings = [];
  const report = strict ? failures : warnings;
  const countFields = [
    "subjects_selected",
    "subjects_written",
    "subjects_failed",
    "chapters_selected",
    "chapters_written",
    "chapters_failed",
  ];

  if (manifest == null) {
    report.push("no prerender-manifest.json — prerender step likely soft-failed");
    return { counts: null, failures, warnings };
  }

  let counts;
  try {
    if (
      typeof manifest !== "object" ||
      Array.isArray(manifest) ||
      manifest.counts == null
    ) {
      throw new Error("manifest must contain a counts object");
    }
    counts = {};
    for (const field of countFields) {
      const value = Number(manifest.counts[field]);
      if (!Number.isInteger(value) || value < 0) {
        throw new Error(`counts.${field} must be a non-negative integer`);
      }
      counts[field] = value;
    }
    if (typeof manifest.budget_exceeded !== "boolean") {
      throw new Error("budget_exceeded must be a boolean");
    }
  } catch (err) {
    report.push(`prerender-manifest.json unreadable: ${err.message}`);
    return { counts: null, failures, warnings };
  }

  if (strict && (counts.subjects_written === 0 || counts.chapters_written === 0)) {
    failures.push(
      `release build requires non-empty subject and chapter prerenders ` +
        `(manifest subjects=${counts.subjects_written}, chapters=${counts.chapters_written})`,
    );
  }
  if (strict && (counts.subjects_failed > 0 || counts.chapters_failed > 0)) {
    failures.push(
      `release prerender reported failures: subjects=${counts.subjects_failed}, ` +
        `chapters=${counts.chapters_failed}`,
    );
  }
  if (strict && manifest.budget_exceeded) {
    failures.push("release prerender exceeded its wall-clock budget");
  }
  if (strict && (counts.subjects_selected === 0 || counts.chapters_selected === 0)) {
    failures.push(
      `release manifest requires non-zero selected coverage ` +
        `(subjects=${counts.subjects_selected}, chapters=${counts.chapters_selected})`,
    );
  }
  if (
    strict &&
    (counts.subjects_written !== counts.subjects_selected ||
      counts.chapters_written !== counts.chapters_selected)
  ) {
    failures.push(
      `release prerender did not complete its selected worklist: ` +
        `subjects=${counts.subjects_written}/${counts.subjects_selected}, ` +
        `chapters=${counts.chapters_written}/${counts.chapters_selected}`,
    );
  }

  return { counts, failures, warnings };
}