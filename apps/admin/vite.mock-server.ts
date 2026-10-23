/**
 * Simule le serveur VDS pour le dev et les tests e2e (le vrai serveur est dans apps/server) :
 * préfixe VDS_BASE_PATH non retiré par le proxy, <base href>, config.js, /health,
 * assets de module sous <base>/modules/<id>/assets/, et un auth.local simulé (jetons, sessions,
 * must_change) sous <base>/api/v1. Aucune logique propre à un mode dans l'admin.
 */
import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { Connect, Plugin } from "vite";
import { createDiffusionMock } from "./vite.mock-diffusion";
import { createPowerMock } from "./vite.mock-power";

const BASE_RE = /^(\/[a-z0-9_-]+)+$/;
const TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".json": "application/json",
  ".js": "text/javascript; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".map": "application/json",
};
const VITE_INTERNAL = ["/@", "/src/", "/node_modules/", "/__"];

export function normalizeBasePath(raw: string | undefined): string {
  const v = (raw ?? "/").trim();
  if (v === "/" || v === "") return "";
  if (!BASE_RE.test(v)) throw new Error(`VDS_BASE_PATH invalide: ${v}`);
  return v;
}

function inside(root: string, rel: string): string | null {
  const full = path.resolve(root, "." + path.posix.normalize("/" + rel));
  return full.startsWith(root + path.sep) && fs.existsSync(full) && fs.statSync(full).isFile() ? full : null;
}

function send(res: ServerResponse, status: number, type: string, body: string | Buffer): void {
  res.statusCode = status;
  res.setHeader("Content-Type", type);
  res.setHeader("Cache-Control", "private, no-store");
  res.end(body);
}

const MOCK_PASSWORD = "correct-horse-battery";
/** Session fixe pour les tests e2e (cookie posé sans passer par le formulaire). */
const E2E_SESSION = "e2e-admin";

interface MockSession {
  name: string;
  email: string;
  mustChange: boolean;
}

function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (c: Buffer) => (raw += c.toString("utf8")));
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw || "{}") as Record<string, unknown>);
      } catch {
        resolve({});
      }
    });
  });
}

function error(res: ServerResponse, status: number, code: string, headers: Record<string, string> = {}): void {
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  send(res, status, TYPES[".json"]!, JSON.stringify({ code, params: {} }));
}

export function vdsMockServer(): Plugin {
  const base = normalizeBasePath(process.env.VDS_BASE_PATH);
  const themeId = process.env.VDS_THEME ?? "theme.vtech";
  const authProvider = process.env.VDS_AUTH_PROVIDER ?? "auth.local";
  const locale = process.env.VDS_DEFAULT_LOCALE ?? "fr";
  const modulesDir = path.resolve(process.env.VDS_MODULES_DIR ?? path.join(__dirname, "../../modules"));
  const manifest = JSON.parse(fs.readFileSync(path.join(modulesDir, themeId, "module.json"), "utf8")) as {
    assets: string;
    theme: { css: string };
  };
  const assetsPrefix = `/modules/${themeId}/${manifest.assets}/`;

  const local = authProvider === "auth.local";
  const sessions = new Map<string, MockSession>([[E2E_SESSION, { name: "Ada", email: "ada@vds.test", mustChange: false }]]);
  const cookiePath = base === "" ? "/" : `${base}/`;

  function sessionOf(req: IncomingMessage): [string, MockSession] | null {
    const m = /(?:^|;\s*)vds_session=([^;]+)/.exec(req.headers.cookie ?? "");
    const token = m?.[1];
    const found = token ? sessions.get(token) : undefined;
    return token && found ? [token, found] : null;
  }

  const diffusionMock = createDiffusionMock();
  const powerMock = createPowerMock(diffusionMock.world);
  const diffusion = async (req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> =>
    (await powerMock(req, res, route, query)) || diffusionMock(req, res, route, query);

  async function api(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<void> {
    const method = req.method ?? "GET";
    if (!local) {
      // Provider hébergé : l'utilisateur est déjà connecté par le Studio.
      if (route === "/me" && method === "GET") {
        return send(res, 200, TYPES[".json"]!, JSON.stringify({ id: "studio-1", display_name: "Camille", email: null, role: "admin", provider: authProvider, locale: null }));
      }
      if (await diffusion(req, res, route, query)) return;
      return error(res, 404, "errors.not_found");
    }
    const sess = sessionOf(req);
    if (!route.startsWith("/m/") && route !== "/me") {
      if (!sess || sess[1].mustChange) return error(res, 401, "auth.unauthenticated");
      if (await diffusion(req, res, route, query)) return;
      return error(res, 404, "errors.not_found");
    }
    const body = method === "POST" ? await readBody(req) : {};
    const ok = (status: number, payload: unknown) => send(res, status, TYPES[".json"]!, JSON.stringify(payload));
    const open = (email: string, name: string, mustChange: boolean) => {
      const token = `s${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
      sessions.set(token, { name, email, mustChange });
      res.setHeader("Set-Cookie", `vds_session=${token}; Path=${cookiePath}; HttpOnly; SameSite=Lax`);
    };

    if (route === "/me" && method === "GET") {
      if (!sess) return error(res, 401, "auth.unauthenticated");
      if (sess[1].mustChange) return error(res, 403, "auth_local.password_change_required");
      return ok(200, { id: "u-1", display_name: sess[1].name, email: sess[1].email, role: "admin", provider: authProvider, locale: null });
    }
    if (route === "/m/auth.local/status" && method === "GET") return ok(200, { initialized: true });
    if (route === "/m/auth.local/bootstrap" && method === "POST") return error(res, 409, "auth_local.already_initialized");
    if (route === "/m/auth.local/login" && method === "POST") {
      const email = String(body.email ?? "").trim().toLowerCase();
      if (email === "locked@vds.test") return error(res, 429, "errors.auth.too_many_attempts", { "Retry-After": "60" });
      const known = email === "ada@vds.test" || email === "temp@vds.test";
      if (!known || body.password !== MOCK_PASSWORD) return error(res, 401, "auth_local.invalid_credentials");
      open(email, email === "temp@vds.test" ? "Temp" : "Ada", email === "temp@vds.test");
      const rt = typeof body.return_to === "string" && (body.return_to === base || body.return_to.startsWith(`${base}/`)) && !body.return_to.startsWith("//");
      return ok(200, { redirect_to: rt ? body.return_to : `${base}/` });
    }
    if (route === "/m/auth.local/logout" && method === "POST") {
      if (sess && sess[0] !== E2E_SESSION) sessions.delete(sess[0]);
      res.setHeader("Set-Cookie", `vds_session=; Path=${cookiePath}; Max-Age=0; HttpOnly; SameSite=Lax`);
      res.statusCode = 204;
      return void res.end();
    }
    if (route === "/m/auth.local/password" && method === "POST") {
      if (!sess) return error(res, 401, "auth.unauthenticated");
      if (body.current !== MOCK_PASSWORD) return error(res, 401, "auth_local.invalid_credentials");
      if (String(body.new ?? "").length < 12) return error(res, 422, "auth_local.password_too_short");
      sess[1].mustChange = false;
      res.statusCode = 204;
      return void res.end();
    }
    return error(res, 404, "errors.not_found");
  }

  const config = {
    basePath: base,
    apiBase: `${base}/api/v1`,
    wsBase: `${base}/ws`,
    defaultLocale: locale,
    version: "0.0.0-dev",
    theme: { id: themeId, cssUrl: `${base}/modules/${themeId}/${manifest.theme.css}`, icons: null, logo: null },
    auth: {
      provider: authProvider,
      loginUrl: local ? `${base}/login?return_to=${encodeURIComponent(`${base}/`)}` : "/espace/connexion",
      logoutUrl: local ? `${base}/api/v1/m/auth.local/logout` : "/espace/deconnexion",
      links: local ? [] : [{ id: "back_to_studio", href: "/espace" }],
    },
  };
  const configJs = `window.__VDS__ = ${JSON.stringify(config)};\n`;
  const baseTag = `<base href="${base}/">`;
  const inject = (html: string): string => html.replace(/<head[^>]*>/i, (m) => m + baseTag);
  const configTag = { tag: "script", attrs: { src: "config.js" }, injectTo: "head-prepend" } as const;

  function handle(distDir: string | null): Connect.NextHandleFunction {
    return (req: IncomingMessage, res: ServerResponse, next: Connect.NextFunction) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const p = url.pathname;
      const outside = base !== "" && p !== base && !p.startsWith(base + "/");
      if (outside) {
        if (!distDir && VITE_INTERNAL.some((x) => p.startsWith(x))) return next();
        return send(res, 404, "text/plain", "errors.not_found");
      }
      const rel = p.slice(base.length) || "/";
      if (rel === "/config.js") return send(res, 200, TYPES[".js"]!, configJs);
      if (rel === "/health") return send(res, 200, TYPES[".json"]!, '{"status":"ok"}');
      if (rel.startsWith("/api/v1/")) return void api(req, res, rel.slice("/api/v1".length), url.searchParams);
      if (rel.startsWith(assetsPrefix)) {
        const f = inside(path.join(modulesDir, themeId, manifest.assets), rel.slice(assetsPrefix.length));
        if (!f) return send(res, 404, "text/plain", "errors.not_found");
        return send(res, 200, TYPES[path.extname(f)] ?? "application/octet-stream", fs.readFileSync(f));
      }
      if (!distDir) {
        req.url = rel + url.search;
        return next();
      }
      const f = inside(distDir, rel);
      if (f && path.basename(f) !== "index.html") {
        return send(res, 200, TYPES[path.extname(f)] ?? "application/octet-stream", fs.readFileSync(f));
      }
      if (/\.[a-z0-9]+$/i.test(rel)) return send(res, 404, "text/plain", "errors.not_found");
      const html = fs.readFileSync(path.join(distDir, "index.html"), "utf8");
      return send(res, 200, TYPES[".html"]!, inject(html));
    };
  }

  return {
    name: "vds-mock-server",
    configureServer(server) {
      server.middlewares.use(handle(null));
    },
    configurePreviewServer(server) {
      server.middlewares.use(handle(path.resolve(server.config.build.outDir)));
    },
    transformIndexHtml: {
      handler(html, ctx) {
        // Build : config.js seul (le serveur injecte <base href>). Dev : on simule aussi le <base>.
        return { html: ctx.server ? inject(html) : html, tags: [configTag] };
      },
    },
  };
}
