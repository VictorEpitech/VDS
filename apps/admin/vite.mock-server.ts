/**
 * Simule le serveur VDS pour le dev et les tests e2e (le vrai serveur est dans apps/server) :
 * préfixe VDS_BASE_PATH non retiré par le proxy, <base href>, config.js, /health,
 * assets de module sous <base>/modules/<id>/assets/. Aucune logique propre à un mode.
 */
import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { Connect, Plugin } from "vite";

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

  const config = {
    basePath: base,
    apiBase: `${base}/api/v1`,
    wsBase: `${base}/ws`,
    defaultLocale: locale,
    version: "0.0.0-dev",
    theme: { id: themeId, cssUrl: `${base}/modules/${themeId}/${manifest.theme.css}`, icons: null, logo: null },
    auth: {
      provider: authProvider,
      loginUrl: null,
      logoutUrl: authProvider === "auth.studio-allia" ? "/espace/deconnexion" : null,
      links: authProvider === "auth.studio-allia" ? [{ id: "back_to_studio", href: "/espace" }] : [],
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
