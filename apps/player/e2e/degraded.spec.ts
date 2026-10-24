import { expect, test } from "@playwright/test";
import type { TestInfo } from "@playwright/test";

/** Mode dégradé (HTTP LAN) : contexte non sécurisé simulé, sans SW ni Cache API. Sous `/` ET `/espace/affichage`. */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };
const origin = (info: TestInfo): string => `http://127.0.0.1:${meta(info).port}`;

async function ctl(info: TestInfo, path: string, body?: unknown): Promise<{
  heartbeats: { errors: { code: string }[] }[];
  mediaHits: Record<string, number>;
}> {
  const res = await fetch(`${origin(info)}/__test/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await res.json()) as never;
}

test("lecture en mémoire via blob:, heartbeat insecure_context, le réseau tombe sans interrompre", async ({ page }, info) => {
  await ctl(info, "reset", {});
  await page.addInitScript(() => {
    Object.defineProperty(window, "isSecureContext", { value: false });
    Object.defineProperty(window, "caches", { value: undefined });
    Object.defineProperty(Navigator.prototype, "serviceWorker", { value: undefined });
  });
  await page.goto(`${meta(info).base}/player/`);
  const code = page.getByTestId("pairing-code");
  await expect(code).toBeVisible();
  await ctl(info, "claim", { code: await code.innerText() });
  const img = page.locator("vds-content-image:visible img");
  await expect(img).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => "serviceWorker" in navigator && !!navigator.serviceWorker)).toBe(false);

  const seen = new Set<string>();
  const sample = async (ms: number): Promise<void> => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const src = await img.first().getAttribute("src", { timeout: 2000 }).catch(() => null);
      if (src) {
        expect(src).toMatch(/^blob:/);
        seen.add(await img.first().evaluate((i: HTMLImageElement) => `${i.naturalWidth > 0}:${i.src}`));
      }
      await page.waitForTimeout(250);
    }
  };
  await sample(6000);
  expect(seen.size).toBeGreaterThanOrEqual(2);
  for (const s of seen) expect(s.startsWith("true:")).toBe(true);

  // Authentification par en-tête et signalement à l'admin seulement.
  await expect
    .poll(async () => (await ctl(info, "state")).heartbeats.some((h) => h.errors.some((e) => e.code === "player.insecure_context")), { timeout: 15_000 })
    .toBe(true);
  expect((await page.locator("#app").innerText()).trim()).toBe("");

  // Réseau coupé : le contenu en mémoire continue.
  await ctl(info, "set", { down: true });
  seen.clear();
  await sample(6000);
  expect(seen.size).toBeGreaterThanOrEqual(2);
  await ctl(info, "set", { down: false });
});
