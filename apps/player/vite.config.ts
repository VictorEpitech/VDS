import { readFileSync } from "node:fs";
import { defineConfig } from "vitest/config";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string };

// base relative : le serveur injecte <base href="<VDS_BASE_PATH>/player/"> dans index.html.
// Cibles faibles (Tizen, WebView TV) : ES2017, aucun framework.
export default defineConfig({
  base: "./",
  build: { outDir: "dist", target: "es2017", sourcemap: false, modulePreload: false },
  define: { __PLAYER_VERSION__: JSON.stringify(pkg.version) },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "test/**/*.test.ts"] },
});
