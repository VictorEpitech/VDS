import { defineConfig } from "@playwright/test";

/** 2 préfixes : un serveur simulé par préfixe (le préfixe est fixé au démarrage du serveur). */
const combos = [
  { name: "root", base: "/", port: 4181 },
  { name: "subpath", base: "/espace/affichage", port: 4182 },
];

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: [["list"]],
  webServer: combos.map((c) => ({
    command: `node e2e/mock-server.mjs --base ${c.base} --port ${c.port} --video e2e/.fixtures/clip.webm`,
    url: `http://127.0.0.1:${c.port}/__test/state`,
    reuseExistingServer: false,
  })),
  projects: combos.map((c) => ({
    name: c.name,
    metadata: { base: c.base === "/" ? "" : c.base, port: c.port },
    use: {
      baseURL: `http://127.0.0.1:${c.port}`,
      browserName: "chromium" as const,
      viewport: { width: 1280, height: 720 },
      launchOptions: { args: ["--autoplay-policy=no-user-gesture-required"] },
    },
  })),
});
