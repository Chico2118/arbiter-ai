/// <reference types="vitest" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// A strict CSP for the production build. The dev server is left without it
// because React Fast Refresh injects an inline script.
const CSP =
  "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; " +
  "script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'";

function cspForBuild(): Plugin {
  return {
    name: "arbiter-csp",
    apply: "build",
    transformIndexHtml: (html) =>
      html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
  };
}

export default defineConfig({
  plugins: [react(), cspForBuild()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
