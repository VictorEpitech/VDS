import { deflateSync } from "node:zlib";
import { expect, test } from "@playwright/test";
import type { TestInfo } from "@playwright/test";

/** Appairage -> claim -> programmation image -> le player lit schedule et médias avec son jeton (vrai serveur). */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };

function crc32(buf: Buffer): number {
  let crc = 0xffffffff;
  for (const b of buf) {
    let c = (crc ^ b) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function png(size = 16): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: size }, () => [200, 30, 30]).flat())]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

/** PDF minimal d'une page (pdfium reconstruit la table xref). */
function pdf(): Buffer {
  return Buffer.from(
    "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
      "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\ntrailer<</Root 1 0 R/Size 4>>\n%%EOF\n",
  );
}

interface DeviceState {
  presence: string;
  heartbeat: { revision: string | null; missing_assets: number } | null;
}

/** Client admin minimal : cookie de session gardé à la main (cookie Secure sur http://127.0.0.1). */
class Admin {
  private cookie = "";
  constructor(
    private readonly origin: string,
    private readonly base: string,
  ) {}
  async call<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
    const isForm = body instanceof FormData;
    const res = await fetch(`${this.origin}${this.base}${path}`, {
      method,
      headers: {
        Origin: this.origin,
        ...(this.cookie ? { Cookie: this.cookie } : {}),
        ...(body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
      },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
    const set = res.headers.getSetCookie();
    if (set.length) this.cookie = set.map((c) => c.split(";")[0]).join("; ");
    const text = await res.text();
    expect([200, 201, 204], `${method} ${path} -> ${res.status} ${text}`).toContain(res.status);
    return (text ? JSON.parse(text) : null) as T;
  }
}

test("appairage, claim, programmation image : le player lit schedule et médias avec son jeton", async ({ page }, info) => {
  const { base, port } = meta(info);
  const origin = `http://127.0.0.1:${port}`;
  const api = new Admin(origin, base);

  await api.call("POST", "/api/v1/m/auth.local/bootstrap", {
    email: "root@vds.test",
    display_name: "Root",
    password: "correct horse battery",
    bootstrap_token: process.env.VDS_E2E_BOOTSTRAP_TOKEN ?? "e2e-bootstrap-token-0123456789",
  });
  const site = await api.call<{ id: string }>("POST", "/api/v1/sites", { name: "Moulins", slug: "moulins" });
  const screen = await api.call<{ id: string; device: unknown }>("POST", "/api/v1/screens", { site_id: site.id, name: "Hall" });
  expect(screen.device).toBeNull();

  const form = new FormData();
  form.append("file", new Blob([png()], { type: "image/png" }), "rouge.png");
  const media = await api.call<{ id: string }>("POST", "/api/v1/media", form);
  await expect
    .poll(async () => (await api.call<{ status: string }>("GET", `/api/v1/media/${media.id}`)).status, { timeout: 30_000 })
    .toBe("ready");
  const playlist = await api.call<{ id: string }>("POST", "/api/v1/playlists", { name: "Accueil" });
  await api.call("POST", `/api/v1/playlists/${playlist.id}/items`, {
    content_type: "content.image",
    props: { media: media.id },
    duration_ms: 3000,
  });
  const doc = new FormData();
  doc.append("file", new Blob([pdf()], { type: "application/pdf" }), "doc.pdf");
  const pdfMedia = await api.call<{ id: string }>("POST", "/api/v1/media", doc);
  await expect
    .poll(async () => (await api.call<{ status: string }>("GET", `/api/v1/media/${pdfMedia.id}`)).status, { timeout: 30_000 })
    .toBe("ready");
  await api.call("POST", `/api/v1/playlists/${playlist.id}/items`, {
    content_type: "content.pdf",
    props: { media: pdfMedia.id, page_duration_ms: 3000 },
  });
  await api.call("POST", "/api/v1/schedules", { name: "Toujours", playlist_id: playlist.id, target_type: "screen", target_id: screen.id });

  // Registre : le content-type actif expose son schéma d'élément à l'admin.
  const types = await api.call<{ id: string; item_schema: object }[]>("GET", "/api/v1/registry/content-types");
  expect(types.find((t) => t.id === "content.image")?.item_schema).toBeTruthy();

  // Le player (vrai build) affiche un code ; l'admin le réclame.
  await page.goto(`${base}/player/`);
  const code = page.getByTestId("pairing-code");
  await expect(code).toHaveText(/^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  await api.call("POST", "/api/v1/pairings/claim", { code: await code.innerText(), screen_id: screen.id });

  const img = page.locator("vds-content-image:visible img");
  await expect(img).toBeVisible({ timeout: 30_000 });
  expect(await img.getAttribute("src")).toMatch(new RegExp(`media/${media.id}/original`));
  expect(await img.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);

  // Le PDF : le serveur résout les pages, le player les lit avec son jeton.
  const pageImg = page.locator("vds-content-pdf:visible img");
  await expect(pageImg).toBeVisible({ timeout: 30_000 });
  expect(await pageImg.getAttribute("src")).toMatch(new RegExp(`media/${pdfMedia.id}/page-1`));
  expect(await pageImg.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);

  // Sans jeton, le média n'est pas servi ; l'admin voit l'appareil en ligne avec la révision active.
  const anon = await fetch(`${origin}${base}/media/${media.id}/original`);
  expect(anon.status).toBe(401);
  await expect
    .poll(async () => (await api.call<{ device: DeviceState }>("GET", `/api/v1/screens/${screen.id}`)).device?.presence, {
      timeout: 30_000,
    })
    .toBe("online");
  const dev = (await api.call<{ device: DeviceState }>("GET", `/api/v1/screens/${screen.id}`)).device;
  expect(dev.heartbeat).not.toBeNull();
});
