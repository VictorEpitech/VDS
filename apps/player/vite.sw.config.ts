import { defineConfig } from "vite";

// Service Worker classique autonome (dist/sw.js), sans chunk partagé avec l'application.
const version = String(Date.now());
export default defineConfig({
  base: "./",
  publicDir: false,
  define: { __SW_VERSION__: JSON.stringify(version) },
  build: {
    outDir: "dist",
    emptyOutDir: false,
    target: "es2019",
    lib: { entry: "src/sw/sw.ts", name: "vdsSw", formats: ["iife"], fileName: () => "sw.js" },
  },
});
