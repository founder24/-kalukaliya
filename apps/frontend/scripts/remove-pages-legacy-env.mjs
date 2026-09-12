#!/usr/bin/env node

const accountId = process.env.CLOUDFLARE_ACCOUNT_ID?.trim();
const token = process.env.CLOUDFLARE_API_TOKEN?.trim();
const projectName =
  process.env.CLOUDFLARE_PAGES_PROJECT?.trim() || "syrabitfrontend";

if (!accountId || !token) {
  throw new Error(
    "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required",
  );
}

async function cloudflare(path, init = {}) {
  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json();
  if (!response.ok || !payload.success) {
    throw new Error(
      `Cloudflare Pages request failed (HTTP ${response.status}): ${JSON.stringify(payload.errors || [])}`,
    );
  }
  return payload.result;
}

const projectPath = `/accounts/${accountId}/pages/projects/${encodeURIComponent(projectName)}`;
const project = await cloudflare(projectPath);
const deploymentConfigs = {};

for (const environment of ["production", "preview"]) {
  const envVars = project.deployment_configs?.[environment]?.env_vars || {};
  if (Object.prototype.hasOwnProperty.call(envVars, "BACKEND_BOT_URL")) {
    deploymentConfigs[environment] = {
      env_vars: { BACKEND_BOT_URL: null },
    };
  }
}

if (Object.keys(deploymentConfigs).length === 0) {
  console.log(
    `BACKEND_BOT_URL is already absent from Cloudflare Pages project ${projectName}.`,
  );
  process.exit(0);
}

await cloudflare(projectPath, {
  method: "PATCH",
  body: JSON.stringify({ deployment_configs: deploymentConfigs }),
});

console.log(
  `Removed BACKEND_BOT_URL from ${Object.keys(deploymentConfigs).join(
    " and ",
  )} configuration for Cloudflare Pages project ${projectName}.`,
);