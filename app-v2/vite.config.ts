import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

/**
 * The API the app talks to. The dev server and the preview server proxy it,
 * so the browser only ever sees one origin. That keeps the CSP simple and
 * avoids CORS entirely.
 */
const API_TARGET = process.env.SOBER_API ?? "http://127.0.0.1:5000";

/**
 * Place search goes to the API's /geocode, which tries Photon, then Nominatim,
 * then an offline list of major cities, within a few seconds. Proxying straight
 * to Nominatim left a search hanging for 21 s whenever Nominatim was
 * unreachable from this network.
 */
const geocodeProxy = {
  target: API_TARGET,
  changeOrigin: true,
};

const apiProxy = {
  target: API_TARGET,
  changeOrigin: true,
  rewrite: (requestPath: string) => requestPath.replace(/^\/api/, ""),
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  optimizeDeps: {
    // MapLibre boots a worker from a sibling module URL. Pre-bundling the
    // package rewrites that URL into `.vite/deps`, where Vite does not emit
    // the worker file, so the map silently loses its WebGL worker. Serving
    // the package source keeps `dist/maplibre-gl-worker.mjs` next to it.
    exclude: ["maplibre-gl"],
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": apiProxy,
      "/geocode": geocodeProxy,
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
    // The preview server serves the real production bundle, so it needs the
    // same proxies as the dev server - otherwise the built app cannot reach
    // the API at all.
    proxy: {
      "/api": apiProxy,
      "/geocode": geocodeProxy,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
