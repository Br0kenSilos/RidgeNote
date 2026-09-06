import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  build: {
    manifest: "manifest.json",
    outDir: "core/static/core/dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(__dirname, "core/static/core/src/app.ts")
      },
      output: {
        entryFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]"
      }
    }
  },
  test: {
    include: ["core/static/core/src/**/*.test.ts"]
  }
});
