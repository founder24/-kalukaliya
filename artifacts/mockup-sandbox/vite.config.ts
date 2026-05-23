import { defineConfig, type UserConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import runtimeErrorOverlay from "@replit/vite-plugin-runtime-error-modal";
import { mockupPreviewPlugin } from "./mockupPreviewPlugin";

const cartographerPlugin =
  process.env.NODE_ENV !== "production" && process.env.REPL_ID !== undefined
    ? await import("@replit/vite-plugin-cartographer").then((m) =>
        m.cartographer({
          root: path.resolve(import.meta.dirname, ".."),
        }),
      )
    : null;

export default defineConfig(({ command }) => {
  const isServe = command === "serve";

  const rawPort = process.env.PORT;
  const port = rawPort ? Number(rawPort) : 5173;

  if (isServe) {
    if (!rawPort) {
      console.warn("PORT not set, defaulting to 5173");
    }

    if (rawPort && (Number.isNaN(port) || port <= 0)) {
      throw new Error(`Invalid PORT value: "${rawPort}"`);
    }

    if (!process.env.BASE_PATH) {
      process.env.BASE_PATH = "/";
    }
  }

  const basePath = process.env.BASE_PATH ?? "/";

  const config: UserConfig = {
    base: basePath,
    plugins: [
      mockupPreviewPlugin(),
      react(),
      tailwindcss(),
      runtimeErrorOverlay(),
      ...(cartographerPlugin ? [cartographerPlugin] : []),
    ],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "src"),
      },
    },
    root: path.resolve(import.meta.dirname),
    build: {
      outDir: path.resolve(import.meta.dirname, "dist"),
      emptyOutDir: true,
    },
    server: {
      port,
      host: "0.0.0.0",
      allowedHosts: true,
      fs: {
        strict: true,
        deny: ["**/.*"],
      },
    },
    preview: {
      port,
      host: "0.0.0.0",
      allowedHosts: true,
    },
  };

  return config;
});
