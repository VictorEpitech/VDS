#!/usr/bin/env node
/**
 * Serveur simulé pour les tests Playwright du player : même contrat que le serveur VDS
 * (appairage, programmation ETag, heartbeat, médias avec Range, jeton d'appareil, 401 device_revoked),
 * sous un préfixe configurable. Commandes de test sous /__test/* (hors préfixe).
 * Usage : node mock-server.mjs --base / --port 4181
 */
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.resolve(here, "../dist");
const MODULES = path.resolve(here, "../../../modules");
const args = process.argv.slice(2);
const arg = (name, def) => (args.includes(name) ? args[args.indexOf(name) + 1] : def);
const BASE = arg("--base", "/");
const PREFIX = BASE === "/" ? "" : BASE;
const PORT = Number(arg("--port", "4181"));
const VIDEO = arg("--video", "");

const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
  "media-src 'self' blob:; font-src 'self'; connect-src 'self'; frame-src https:; worker-src 'self'; " +
  "object-src 'none'; base-uri 'self'";

// --- médias de test ---------------------------------------------------------
function crc32(buf) {
  let c;
  let crc = 0xffffffff;
  for (const b of buf) {
    c = (crc ^ b) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function png(r, g, b, size = 16) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: size }, () => [r, g, b]).flat())]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
const MEDIA = {
  red: { buf: png(255, 0, 0), mime: "image/png" },
  blue: { buf: png(0, 0, 255), mime: "image/png" },
};
const sha = (buf) => createHash("sha256").update(buf).digest("hex");
for (const m of Object.values(MEDIA)) m.sha = sha(m.buf);
/** Le clip vidéo est fabriqué par le globalSetup, après le démarrage du serveur : chargement paresseux. */
function mediaOf(id) {
  if (id === "clip" && !MEDIA.clip && VIDEO && fs.existsSync(VIDEO)) {
    const buf = fs.readFileSync(VIDEO);
    MEDIA.clip = { buf, mime: "video/webm", sha: sha(buf) };
  }
  return MEDIA[id];
}

// --- état -------------------------------------------------------------------
const fresh = () => ({
  clock: null, // { base, at } : horloge simulée du serveur
  down: false,
  pairing: null,
  token: null,
  revoked: false,
  items: [
    { id: "it_red", media: "red", ms: 2000 },
    { id: "it_blue", media: "blue", ms: 2000 },
  ],
  slots: null, // [[h0, h1, kind]] en heures depuis minuit UTC du jour simulé ; null = tout play
  heartbeats: [],
  mediaHits: {},
  hits: 0,
  rejected: 0,
});
let S = fresh();
const serverNow = () => (S.clock ? S.clock.base + (Date.now() - S.clock.at) : Date.now());
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "+00:00");

function schedule() {
  const now = serverNow();
  const day = Math.floor(now / 86400000) * 86400000;
  const slots = (S.slots ?? [[0, 48, "play"]]).map(([a, b, kind]) =>
    kind === "off"
      ? { start: iso(day + a * 3600000), end: iso(day + b * 3600000), kind: "off", mode: "black" }
      : {
          start: iso(day + a * 3600000),
          end: iso(day + b * 3600000),
          kind: "play",
          layout: { zones: [{ id: "main", x: 0, y: 0, w: 1, h: 1, playlist: "pl_1" }] },
          source: { schedule_id: "sc_1" },
        },
  );
  const used = [...new Set(S.items.map((i) => i.media))];
  const doc = {
    format: 1,
    screen: { id: "screen-1", name: "Accueil", orientation: "landscape" },
    timezone: "UTC",
    valid_from: iso(day),
    valid_until: iso(day + 48 * 3600000),
    slots,
    playlists: {
      pl_1: {
        items: S.items.map((i) => ({
          id: i.id,
          content_type: mediaOf(i.media).mime.startsWith("video") ? "content.video" : "content.image",
          duration_ms: i.ms ?? null,
          props: { src: `media/${i.media}/original` },
          assets: [`media/${i.media}/original`],
          requires_network: false,
        })),
      },
    },
    assets: Object.fromEntries(
      used.map((m) => [`media/${m}/original`, { sha256: mediaOf(m).sha, bytes: mediaOf(m).buf.length, mime: mediaOf(m).mime }]),
    ),
    content_types: {
      "content.image": { version: "0.1.0", element: "vds-content-image", script: "modules/content.image/assets/player.js" },
      "content.video": { version: "0.1.0", element: "vds-content-video", script: "modules/content.video/assets/player.js" },
    },
  };
  const revision = "sha256:" + createHash("sha256").update(JSON.stringify(doc)).digest("hex");
  return { ...doc, revision, server_time: new Date(now).toISOString() };
}

// --- HTTP -------------------------------------------------------------------
const MIME = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".json": "application/json" };
const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "private, no-store", ...headers });
  res.end(JSON.stringify(body));
};
const readBody = (req) =>
  new Promise((resolve) => {
    const parts = [];
    req.on("data", (c) => parts.push(c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(parts).toString() || "{}"));
      } catch {
        resolve({});
      }
    });
  });
const bearer = (req) => /^Bearer (.+)$/i.exec(req.headers.authorization ?? "")?.[1] ?? null;

function sendFile(res, file, extra = {}) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return json(res, 404, { code: "errors.not_found" });
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] ?? "application/octet-stream", "Cache-Control": "private, no-store", ...extra });
  fs.createReadStream(file).pipe(res);
}

function deviceAuth(req, res) {
  const t = bearer(req);
  if (!t || t !== S.token) {
    json(res, 401, { code: "errors.device_unauthorized", params: {} });
    return false;
  }
  if (S.revoked) {
    json(res, 401, { code: "errors.device_revoked", params: {} });
    return false;
  }
  return true;
}

async function control(req, res, url) {
  const body = req.method === "POST" ? await readBody(req) : {};
  switch (url.pathname) {
    case "/__test/reset":
      S = fresh();
      return json(res, 200, {});
    case "/__test/state":
      return json(res, 200, { down: S.down, heartbeats: S.heartbeats, mediaHits: S.mediaHits, hits: S.hits, rejected: S.rejected, pairing: S.pairing && { code: S.pairing.code } });
    case "/__test/set":
      if ("down" in body) S.down = Boolean(body.down);
      if ("revoked" in body) S.revoked = Boolean(body.revoked);
      if ("slots" in body) S.slots = body.slots;
      if ("items" in body) S.items = body.items;
      if ("clock" in body) S.clock = body.clock ? { base: Date.parse(body.clock), at: Date.now() } : null;
      return json(res, 200, {});
    case "/__test/claim": {
      const code = String(body.code ?? "").replace("-", "");
      if (!S.pairing || S.pairing.code.replace("-", "") !== code) return json(res, 404, {});
      S.pairing.claimed = true;
      S.token = "vds_dev_" + randomBytes(24).toString("base64url");
      S.revoked = false;
      return json(res, 200, {});
    }
    default:
      return json(res, 404, {});
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname.startsWith("/__test/")) return control(req, res, url);
  S.hits++;
  if (S.down) {
    S.rejected++;
    req.socket.destroy();
    return;
  }
  if (PREFIX && !(url.pathname === PREFIX || url.pathname.startsWith(PREFIX + "/"))) return json(res, 404, {});
  const p = url.pathname.slice(PREFIX.length) || "/";

  if (p === "/health") return json(res, 200, { status: "ok" });
  if (p === "/config.js") {
    const cfg = {
      basePath: PREFIX,
      apiBase: `${PREFIX}/api/v1`,
      wsBase: `${PREFIX}/ws`,
      defaultLocale: "fr",
      version: "test",
      theme: { id: "theme.vtech", cssUrl: `${PREFIX}/modules/theme.vtech/assets/theme.css`, icons: null, logo: null },
      auth: { provider: "auth.local", loginUrl: null, logoutUrl: null, links: [] },
    };
    res.writeHead(200, { "Content-Type": "text/javascript", "Cache-Control": "private, no-store" });
    return res.end("window.__VDS__ = " + JSON.stringify(cfg) + ";\n");
  }
  if (p === "/player") {
    res.writeHead(307, { Location: `${PREFIX}/player/` });
    return res.end();
  }
  if (p.startsWith("/player/")) {
    const rel = p.slice("/player/".length);
    if (rel === "") {
      const html = fs.readFileSync(path.join(DIST, "index.html"), "utf8").replace(/<head[^>]*>/i, (m) => `${m}<base href="${PREFIX}/player/">`);
      res.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "private, no-store", "Content-Security-Policy": CSP });
      return res.end(html);
    }
    const file = path.resolve(DIST, rel);
    if (!file.startsWith(DIST + path.sep)) return json(res, 404, {});
    return sendFile(res, file, { "Content-Security-Policy": CSP });
  }
  const mod = /^\/modules\/([a-z0-9.-]+)\/assets\/(.+)$/.exec(p);
  if (mod && /^(theme|content)\./.test(mod[1])) {
    const root = path.join(MODULES, mod[1], "assets");
    const file = path.resolve(root, mod[2]);
    if (!file.startsWith(root + path.sep)) return json(res, 404, {});
    return sendFile(res, file);
  }

  if (p === "/api/v1/player/pairings" && req.method === "POST") {
    const alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789";
    const raw = Array.from({ length: 6 }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join("");
    S.pairing = { id: randomBytes(8).toString("hex"), code: `${raw.slice(0, 3)}-${raw.slice(3)}`, secret: randomBytes(18).toString("base64url"), claimed: false, consumed: false };
    return json(res, 201, { pairing_id: S.pairing.id, code: S.pairing.code, poll_secret: S.pairing.secret, expires_at: iso(Date.now() + 900000), poll_interval_s: 1 });
  }
  const poll = /^\/api\/v1\/player\/pairings\/([a-f0-9]+)$/.exec(p);
  if (poll) {
    const pr = S.pairing;
    if (!pr || pr.id !== poll[1] || bearer(req) !== pr.secret || pr.consumed) return json(res, 410, { code: "errors.pairing_expired" });
    if (!pr.claimed) return json(res, 202, { status: "pending" });
    pr.consumed = true;
    return json(res, 200, { device_token: S.token, screen_id: "screen-1", server_time: new Date(serverNow()).toISOString() });
  }
  if (p === "/api/v1/player/schedule") {
    if (!deviceAuth(req, res)) return;
    const doc = schedule();
    const etag = `"${doc.revision}"`;
    if (req.headers["if-none-match"] === etag) {
      res.writeHead(304, { ETag: etag });
      return res.end();
    }
    return json(res, 200, doc, { ETag: etag });
  }
  if (p === "/api/v1/player/heartbeat" && req.method === "POST") {
    if (!deviceAuth(req, res)) return;
    const body = await readBody(req);
    S.heartbeats.push(body);
    return json(res, 200, { revision: schedule().revision, server_time: new Date(serverNow()).toISOString(), poll_interval_s: 2, commands: [] });
  }
  const media = /^\/media\/([a-z0-9-]+)\/([a-z0-9-]+)$/.exec(p);
  if (media) {
    if (!deviceAuth(req, res)) return;
    const m = mediaOf(media[1]);
    if (!m || media[2] !== "original") return json(res, 404, { code: "media.not_found" });
    S.mediaHits[media[1]] = (S.mediaHits[media[1]] ?? 0) + 1;
    const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
    if (range) {
      const start = range[1] === "" ? m.buf.length - Number(range[2]) : Number(range[1]);
      const end = range[1] !== "" && range[2] !== "" ? Math.min(Number(range[2]), m.buf.length - 1) : m.buf.length - 1;
      res.writeHead(206, { "Content-Type": m.mime, "Content-Range": `bytes ${start}-${end}/${m.buf.length}`, "Content-Length": end - start + 1, "Accept-Ranges": "bytes", ETag: `"${m.sha}"` });
      return res.end(m.buf.subarray(start, end + 1));
    }
    res.writeHead(200, { "Content-Type": m.mime, "Content-Length": m.buf.length, "Accept-Ranges": "bytes", ETag: `"${m.sha}"`, "Cache-Control": "private, no-store" });
    return res.end(m.buf);
  }
  return json(res, 404, { code: "errors.not_found" });
});

server.listen(PORT, "127.0.0.1", () => console.log(`mock vds on ${PORT} base=${BASE}`));
