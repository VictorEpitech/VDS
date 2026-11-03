import { expect, test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

/** Commandes player (lot I, I4) contre le serveur simulé, sous `/` ET `/espace/affichage`. */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };
const origin = (info: TestInfo): string => `http://127.0.0.1:${meta(info).port}`;

interface Result { via: string; cmd_id: string; status: string; code: string | null; data: Record<string, unknown> }
interface State {
  heartbeats: { boot_id?: string; capabilities?: string[] }[];
  results: Result[];
  uploads: unknown[];
}

async function ctl(info: TestInfo, path: string, body?: unknown): Promise<State> {
  const res = await fetch(`${origin(info)}/__test/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await res.json()) as State;
}
const playerUrl = (info: TestInfo): string => `${meta(info).base}/player/`;
const img = (page: Page) => page.locator("vds-content-image:visible img");
const uuid = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const result = async (info: TestInfo, id: string): Promise<Result | undefined> =>
  (await ctl(info, "state")).results.find((r) => r.cmd_id === id);

async function pairAndPlay(page: Page, info: TestInfo): Promise<void> {
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toHaveText(/^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  await ctl(info, "claim", { code: await code.innerText() });
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
}

// eslint-disable-next-line no-empty-pattern -- Playwright impose la déstructuration
test.beforeEach(async ({}, info) => {
  await ctl(info, "reset", {});
});

test("heartbeat : boot_id et capacités de l'adaptateur web (pas de capture)", async ({ page }, info) => {
  await pairAndPlay(page, info);
  await expect.poll(async () => (await ctl(info, "state")).heartbeats.at(-1)?.boot_id, { timeout: 15_000 }).toMatch(/^[0-9a-f-]{36}$/);
  const hb = (await ctl(info, "state")).heartbeats.at(-1)!;
  expect(hb.capabilities).toEqual(["cmd.reload", "cmd.identify", "cmd.cache.resync"]);
});

test("identify par WS : le nom seul, puis reprise ; résultat ok ; rien de technique", async ({ page }, info) => {
  await pairAndPlay(page, info);
  await expect.poll(async () => (await ctl(info, "state")).heartbeats.length, { timeout: 15_000 }).toBeGreaterThan(0);
  await expect.poll(async () => (await fetch(`${origin(info)}/__test/state`).then((r) => r.json()) as { ws: { open: number } }).ws.open).toBe(1);
  await ctl(info, "cmd", { cmd_id: uuid(1), name: "identify", args: { seconds: 5 } });
  const ident = page.locator(".identify");
  await expect(ident).toBeVisible();
  const text = (await ident.innerText()).trim();
  expect(text.length).toBeGreaterThan(0);
  expect(text).not.toMatch(/[0-9a-f]{8}-|http|screen[-_ ]?id|v\d+\.\d+|\d+\.\d+\.\d+/i);
  await expect(ident).toBeHidden({ timeout: 15_000 });
  await expect.poll(async () => (await result(info, uuid(1)))?.status, { timeout: 10_000 }).toBe("ok");
  await expect(img(page)).toBeVisible();
});

test("reload par repli HTTP : une seule exécution malgré la double livraison, accusé reçu", async ({ page }, info) => {
  await ctl(info, "set", { wsBlocked: true, pollS: 1 });
  await pairAndPlay(page, info);
  await ctl(info, "cmd", { cmd_id: uuid(2), name: "reload", via: "http" });
  await expect.poll(async () => (await result(info, uuid(2)))?.status, { timeout: 30_000 }).toBe("ok");
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(4000);
  const st = await ctl(info, "state");
  const boots = new Set(st.heartbeats.map((h) => h.boot_id));
  expect(boots.size).toBe(2); // un seul rechargement : avant / après
  expect(st.results.filter((r) => r.cmd_id === uuid(2) && r.status === "ok").length).toBeGreaterThan(0);
});

test("commande redélivrée après exécution : jamais réexécutée", async ({ page }, info) => {
  await ctl(info, "set", { wsBlocked: true, pollS: 1 });
  await pairAndPlay(page, info);
  await ctl(info, "cmd", { cmd_id: uuid(3), name: "reload", via: "http" });
  await expect.poll(async () => (await result(info, uuid(3)))?.status, { timeout: 30_000 }).toBe("ok");
  await ctl(info, "cmd", { cmd_id: uuid(3), name: "reload", via: "http" }); // rejeu serveur
  await page.waitForTimeout(5000);
  const boots = new Set((await ctl(info, "state")).heartbeats.map((h) => h.boot_id));
  expect(boots.size).toBe(2);
});

test("commande expirée, inconnue ou non supportée : rejetée sans effet visible", async ({ page }, info) => {
  await pairAndPlay(page, info);
  await expect.poll(async () => (await fetch(`${origin(info)}/__test/state`).then((r) => r.json()) as { ws: { open: number } }).ws.open).toBe(1);
  await ctl(info, "cmd", { cmd_id: uuid(4), name: "reload", expires_at: new Date(Date.now() - 60_000).toISOString() });
  await ctl(info, "cmd", { cmd_id: uuid(5), name: "screenshot" });
  await ctl(info, "cmd", { cmd_id: uuid(6), name: "explode" });
  await expect.poll(async () => (await ctl(info, "state")).results.length, { timeout: 10_000 }).toBe(3);
  expect((await result(info, uuid(4)))?.status).toBe("expired");
  expect(await result(info, uuid(5))).toMatchObject({ status: "rejected", code: "unsupported" });
  expect(await result(info, uuid(6))).toMatchObject({ status: "rejected", code: "unknown_command" });
  await expect(img(page)).toBeVisible();
  expect(await page.locator("body").innerText()).not.toMatch(/unsupported|unknown|expired|screenshot|capture/i);
  expect((await ctl(info, "state")).uploads).toEqual([]);
});

test("hors ligne : le player continue, la commande arrivée à la reconnexion est exécutée", async ({ page }, info) => {
  test.setTimeout(120_000); // après une coupure, le prochain heartbeat attend l'intervalle par défaut (60 s)
  await pairAndPlay(page, info);
  await ctl(info, "set", { down: true });
  await page.waitForTimeout(3000);
  await expect(img(page)).toBeVisible(); // lecture sans serveur
  await ctl(info, "set", { down: false, wsBlocked: true, pollS: 1 });
  await ctl(info, "cmd", { cmd_id: uuid(7), name: "cache.resync", via: "http" });
  await expect.poll(async () => (await result(info, uuid(7)))?.status, { timeout: 90_000 }).toBe("ok");
  await expect(img(page)).toBeVisible();
});
