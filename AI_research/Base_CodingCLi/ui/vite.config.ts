import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";
import type { Plugin } from "vite";

// Chunks of earlier builds stay for tabs opened before a rollout, but only this long.
const RETAIN_DAYS = 7;

/** Delete hashed assets that this build did not write and nobody has needed for RETAIN_DAYS. */
function pruneStaleAssets(): Plugin {
  return {
    name: "prune-stale-assets",
    apply: "build",
    writeBundle(options, bundle) {
      const assets = path.join(options.dir ?? "dist", "assets");
      const current = new Set(Object.keys(bundle).map((name) => path.basename(name)));
      const cutoff = Date.now() - RETAIN_DAYS * 86400_000;
      for (const name of fs.existsSync(assets) ? fs.readdirSync(assets) : []) {
        const file = path.join(assets, name);
        if (!current.has(name) && fs.statSync(file).mtimeMs < cutoff) fs.rmSync(file, { force: true });
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), pruneStaleAssets()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    // Retain hashed chunks for tabs that were already open during a UI rollout; pruneStaleAssets bounds them.
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-markdown": ["react-markdown", "remark-gfm"],
          "vendor-state": ["zustand"],
        },
      },
    },
  },
});
