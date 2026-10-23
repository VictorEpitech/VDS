import { defineConfig } from "@playwright/test";

/** Vrai serveur VDS + vrai build du player, un serveur (et un PG dédié) par préfixe. */
const combos = [
  { name: "root", base: "/", port: 4191 },
  { name: "subpath", base: "/espace/affichage", port: 4192 },
];

export default defineConfig({
  testDir: "./e2e-real",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  reporter: [["list"]],
  webServer: combos.map((c) => ({
    command: `node e2e-real/real-server.mjs --base ${c.base} --port ${c.port}`,
    url: `http://127.0.0.1:${c.port}${c.base === "/" ? "" : c.base}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM" as const, timeout: 20_000 }, // laisse le script supprimer le conteneur PG
  })),
  projects: combos.map((c) => ({
    name: c.name,
    metadata: { base: c.base === "/" ? "" : c.base, port: c.port },
    use: { baseURL: `http://127.0.0.1:${c.port}`, browserName: "chromium" as const, viewport: { width: 1280, height: 720 } },
  })),
});
