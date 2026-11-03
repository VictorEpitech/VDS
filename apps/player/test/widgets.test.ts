import { beforeAll, describe, expect, it } from "vitest";

/** Éléments `vds-content-weather` / `vds-content-rss` : validation, péremption, langue, texte brut (jsdom). */
const WEATHER = "../../../modules/content.weather/assets/player.js";
const RSS = "../../../modules/content.rss/assets/player.js";

interface El extends HTMLElement {
  configure(p: unknown, c: unknown): void;
  prepare(): Promise<void>;
  start(): void;
  stop(): void;
}

const NOW = Date.parse("2026-11-03T12:00:00Z");
const wData = {
  now: { temp_c: 20, symbol: "clearsky_night" },
  days: [{ date: "2026-11-03", min_c: 0, max_c: 10, symbol: "snow" }],
};
const rData = { items: [{ title: "<script>x</script>Titre", summary: "<b>gras</b>", published: "2026-11-03T08:00:00Z" }] };

function ctx(data: unknown, fetchedAt: string | null, locale = "fr", now = NOW) {
  return {
    locale,
    orientation: "landscape",
    zone: { width: 800, height: 450 },
    resolveUrl: (x: string) => x,
    now: () => now,
    feed: async (id: string) => (fetchedAt === null ? null : { feedId: id, fetchedAt, data }),
  };
}

async function make(tag: string, props: unknown, c: unknown): Promise<El> {
  const el = document.createElement(tag) as El;
  el.configure(props, c);
  document.body.append(el);
  return el;
}

beforeAll(async () => {
  await import(/* @vite-ignore */ WEATHER);
  await import(/* @vite-ignore */ RSS);
});

describe("vds-content-weather", () => {
  const props = { label: "Lyon", days: 1, feed_id: "f".repeat(32), max_age_s: 3600 };

  it("rend température, libellé et mention, en français puis en anglais, en °F si impérial", async () => {
    const fr = await make("vds-content-weather", props, ctx(wData, "2026-11-03T11:30:00Z"));
    await fr.prepare();
    expect(fr.textContent).toContain("20°");
    expect(fr.textContent).toContain("Lyon");
    expect(fr.textContent).toContain("Données MET Norway");
    const en = await make("vds-content-weather", { ...props, units: "imperial" }, ctx(wData, "2026-11-03T11:30:00Z", "en"));
    await en.prepare();
    expect(en.textContent).toContain("68°");
    expect(en.textContent).toContain("Data from MET Norway");
    fr.stop();
    expect(fr.childElementCount).toBe(0);
  });

  it("jamais reçu = feed_unavailable ; forme inattendue = feed_unavailable ; trop ancien = feed_stale", async () => {
    const a = await make("vds-content-weather", props, ctx(wData, null));
    await expect(a.prepare()).rejects.toThrow("feed_unavailable");
    const b = await make("vds-content-weather", props, ctx({ now: { temp_c: "x" } }, "2026-11-03T11:59:00Z"));
    await expect(b.prepare()).rejects.toThrow("feed_unavailable");
    const c = await make("vds-content-weather", props, ctx(wData, "2026-11-03T10:59:59Z"));
    await expect(c.prepare()).rejects.toThrow("feed_stale");
    const d = await make("vds-content-weather", props, ctx(wData, "n'importe quoi"));
    await expect(d.prepare()).rejects.toThrow("feed_stale");
  });

  it("l'âge se juge sur l'horloge du player (ctx.now), pas sur Date.now()", async () => {
    const e = await make("vds-content-weather", props, ctx(wData, "2026-11-03T11:30:00Z", "fr", NOW + 7200_000));
    await expect(e.prepare()).rejects.toThrow("feed_stale");
  });

  it("symbole inconnu : pictogramme de repli, pas d'erreur", async () => {
    const e = await make("vds-content-weather", props, ctx({ ...wData, now: { temp_c: 1, symbol: "inconnu_xyz" } }, "2026-11-03T11:59:00Z"));
    await e.prepare();
    expect(e.querySelector("img")?.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
  });
});

describe("vds-content-rss", () => {
  const props = { display: "list", max_items: 3, item_duration_s: 4, feed_id: "e".repeat(32), max_age_s: 3600 };

  it("insère du texte brut : aucun nœud créé depuis le balisage du flux", async () => {
    const el = await make("vds-content-rss", props, ctx(rData, "2026-11-03T11:30:00Z"));
    await el.prepare();
    expect(el.textContent).toContain("<script>x</script>Titre");
    expect(el.textContent).toContain("<b>gras</b>");
    expect(el.querySelector("script, b, img")).toBeNull();
    el.stop();
    expect(el.childElementCount).toBe(0);
  });

  it("vide, absent ou ancien : l'élément échoue proprement", async () => {
    await expect((await make("vds-content-rss", props, ctx({ items: [] }, "2026-11-03T11:59:00Z"))).prepare()).rejects.toThrow("feed_unavailable");
    await expect((await make("vds-content-rss", props, ctx(rData, null))).prepare()).rejects.toThrow("feed_unavailable");
    await expect((await make("vds-content-rss", props, ctx(rData, "2026-11-02T00:00:00Z"))).prepare()).rejects.toThrow("feed_stale");
  });

  it("stop() libère les minuteries (rotation de pages)", async () => {
    const many = { items: Array.from({ length: 10 }, (_, i) => ({ title: `T${i}`, summary: "s", published: null })) };
    const el = await make("vds-content-rss", { ...props, max_items: 10 }, ctx(many, "2026-11-03T11:59:00Z"));
    await el.prepare();
    el.start();
    el.stop();
    expect(el.childElementCount).toBe(0);
  });
});
