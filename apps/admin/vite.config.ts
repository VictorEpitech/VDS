import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { vdsMockServer } from "./vite.mock-server";

// base relative : le serveur injecte <base href="<VDS_BASE_PATH>/"> dans index.html.
export default defineConfig({
  base: "./",
  plugins: [react(), vdsMockServer()],
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "test/**/*.test.{ts,tsx}"],
    css: false,
  },
});
