import { defineConfig } from "@playwright/test";

/** 2 préfixes x 2 thèmes : un serveur simulé par combinaison (le préfixe et le thème sont lus au démarrage). */
const combos = [
  { name: "root-vtech", base: "/", theme: "theme.vtech", auth: "auth.local", port: 4171 },
  { name: "subpath-vtech", base: "/espace/affichage", theme: "theme.vtech", auth: "auth.local", port: 4172 },
  { name: "root-allia", base: "/", theme: "theme.allia", auth: "auth.studio-allia", port: 4173 },
  { name: "subpath-allia", base: "/espace/affichage", theme: "theme.allia", auth: "auth.studio-allia", port: 4174 },
];

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: [["list"]],
  webServer: combos.map((c) => ({
    command: `node_modules/.bin/vite preview --port ${c.port} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${c.port}${c.base === "/" ? "" : c.base}/health`,
    reuseExistingServer: false,
    env: { VDS_BASE_PATH: c.base, VDS_THEME: c.theme, VDS_AUTH_PROVIDER: c.auth },
  })),
  projects: combos.map((c) => ({
    name: c.name,
    metadata: { base: c.base === "/" ? "" : c.base, theme: c.theme, auth: c.auth },
    use: {
      baseURL: `http://127.0.0.1:${c.port}`,
      browserName: "chromium" as const,
      viewport: { width: 1280, height: 800 },
    },
  })),
});
