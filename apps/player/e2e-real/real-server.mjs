#!/usr/bin/env node
/**
 * VRAI serveur VDS (uvicorn) + PostgreSQL 17 dédié (conteneur jetable, mot de passe aléatoire),
 * servant le VRAI build du player (apps/player/dist) sous le préfixe demandé.
 * Usage : node real-server.mjs --base /espace/affichage --port 4191
 * Le conteneur, la base et le répertoire média sont supprimés à l'arrêt (SIGTERM/SIGINT).
 */
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, "../../..");
const args = process.argv.slice(2);
const arg = (name, def) => (args.includes(name) ? args[args.indexOf(name) + 1] : def);
const BASE = arg("--base", "/");
const PORT = Number(arg("--port", "4191"));
const PG_PORT = PORT + 1000;
const NAME = `vds-e2e-pg-${PORT}`;
const PASSWORD = randomBytes(18).toString("base64url");
const BOOTSTRAP_TOKEN = process.env.VDS_E2E_BOOTSTRAP_TOKEN ?? "e2e-bootstrap-token-0123456789";
const env = { ...process.env, PATH: `${os.homedir()}/.local/bin:${process.env.PATH}` };
const media = fs.mkdtempSync(path.join(os.tmpdir(), "vds-e2e-media-"));

const sh = (cmd, a, opts = {}) => spawnSync(cmd, a, { encoding: "utf8", env, ...opts });
let server;
let cleaned = false;
function cleanup() {
  if (cleaned) return;
  cleaned = true;
  server?.kill("SIGTERM");
  sh("docker", ["rm", "-f", "-v", NAME]);
  fs.rmSync(media, { recursive: true, force: true });
}
for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  process.on(sig, () => {
    cleanup();
    process.exit(0);
  });
}
process.on("exit", cleanup);

sh("docker", ["rm", "-f", "-v", NAME]);
const run = sh("docker", [
  "run", "-d", "--name", NAME,
  "-e", `POSTGRES_PASSWORD=${PASSWORD}`, "-e", "POSTGRES_USER=vds", "-e", "POSTGRES_DB=vds_e2e",
  "-p", `127.0.0.1:${PG_PORT}:5432`, "postgres:17",
]);
if (run.status !== 0) {
  console.error(run.stderr);
  process.exit(1);
}
for (let i = 0; ; i++) {
  // pg_isready répond avant la fin de l'initialisation : exiger une vraie requête TCP.
  const r = sh("docker", ["exec", NAME, "psql", "-h", "127.0.0.1", "-U", "vds", "-d", "vds_e2e", "-tAc", "select 1"], {
    env: { ...env, PGPASSWORD: PASSWORD },
  });
  if (r.status === 0 && r.stdout.trim() === "1") break;
  if (i > 60) {
    console.error("PostgreSQL ne démarre pas");
    process.exit(1);
  }
  await new Promise((resolve) => setTimeout(resolve, 1000));
}

const srvEnv = {
  ...env,
  VDS_BASE_PATH: BASE,
  VDS_DATABASE_URL: `postgresql+asyncpg://vds:${PASSWORD}@127.0.0.1:${PG_PORT}/vds_e2e`,
  VDS_PUBLIC_ORIGIN: `http://127.0.0.1:${PORT}`,
  VDS_PLAYER_DIR: path.join(ROOT, "apps/player/dist"),
  VDS_MODULES_DIR: path.join(ROOT, "modules"),
  VDS_MEDIA_DIR: media,
  VDS_BOOTSTRAP_TOKEN: BOOTSTRAP_TOKEN,
};
const mig = spawnSync("uv", ["run", "python", "-m", "vds_server.migrate"], { cwd: ROOT, env: srvEnv, stdio: "inherit" });
if (mig.status !== 0) process.exit(1);
server = spawn("uv", ["run", "uvicorn", "vds_server.main:app_factory", "--factory", "--host", "127.0.0.1", "--port", String(PORT)], {
  cwd: ROOT,
  env: srvEnv,
  stdio: "inherit",
});
server.on("exit", (code) => {
  cleanup();
  process.exit(code ?? 1);
});
