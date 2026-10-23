// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ContentContext, VdsContentElement } from "./index";

const MODULES = ["content.image", "content.video", "content.pdf", "content.url"] as const;

const ctx: ContentContext = {
  locale: "fr",
  orientation: "landscape",
  zone: { width: 1920, height: 1080 },
  resolveUrl: (relative) => `https://srv/espace/affichage/${relative}`
};

function manifestOf(id: string): { content: { player: { element: string; script: string } } } {
  return JSON.parse(readFileSync(resolve(process.cwd(), "../../modules", id, "module.json"), "utf-8"));
}

async function load(id: string): Promise<void> {
  const path = `../../../modules/${id}/${manifestOf(id).content.player.script}`;
  await import(/* @vite-ignore */ path);
}

function make(id: string): VdsContentElement {
  const el = document.createElement(manifestOf(id).content.player.element) as VdsContentElement;
  document.body.append(el);
  return el;
}

function events(el: HTMLElement): { ended: number; errors: string[] } {
  const seen = { ended: 0, errors: [] as string[] };
  el.addEventListener("vds-ended", () => (seen.ended += 1));
  el.addEventListener("vds-error", (e) => seen.errors.push((e as CustomEvent<{ code: string }>).detail.code));
  return seen;
}

let decodeResult: () => Promise<void> = () => Promise.resolve();
const played: HTMLMediaElement[] = [];

beforeAll(async () => {
  Object.defineProperty(HTMLImageElement.prototype, "decode", {
    configurable: true,
    value: () => decodeResult()
  });
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value(this: HTMLMediaElement) {
      played.push(this);
      return Promise.resolve();
    }
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: () => {} });
  Object.defineProperty(HTMLMediaElement.prototype, "load", { configurable: true, value: () => {} });
  for (const id of MODULES) await load(id);
});

afterEach(() => {
  document.body.replaceChildren();
  decodeResult = () => Promise.resolve();
  played.length = 0;
  vi.useRealTimers();
});

describe.each(MODULES)("%s : contrat d'élément", (id) => {
  it("enregistre l'élément déclaré par le manifeste", () => {
    const el = make(id);
    expect(customElements.get(manifestOf(id).content.player.element)).toBeDefined();
    for (const m of ["configure", "prepare", "start", "stop"] as const) {
      expect(typeof el[m]).toBe("function");
    }
  });

  it("stop() est sûr sans prepare et masque l'élément", () => {
    const el = make(id);
    el.configure({}, ctx);
    expect(() => el.stop()).not.toThrow();
    expect(el.style.visibility).toBe("hidden");
  });
});

describe("content.image", () => {
  it("lit la clé `media` du schéma d'élément (propriété réelle émise par le serveur)", async () => {
    const el = make("content.image");
    el.configure({ media: "media/m2/original" }, ctx);
    await el.prepare();
    expect(el.querySelector("img")?.getAttribute("src")).toBe("https://srv/espace/affichage/media/m2/original");
  });

  it("affiche l'image décodée avec l'ajustement demandé", async () => {
    const el = make("content.image");
    el.configure({ src: "media/m1/original", fit: "cover" }, ctx);
    await el.prepare();
    const img = el.querySelector("img");
    expect(img?.getAttribute("src")).toBe("https://srv/espace/affichage/media/m1/original");
    expect(img?.style.objectFit).toBe("cover");
    el.start();
    expect(el.style.visibility).toBe("visible");
    el.stop();
    expect(el.childElementCount).toBe(0);
  });

  it("rejette prepare si le décodage échoue, sans message technique", async () => {
    decodeResult = () => Promise.reject(new Error("boom"));
    const el = make("content.image");
    el.configure({ src: "media/m1/original", fit: "nimportequoi" }, ctx);
    await expect(el.prepare()).rejects.toThrow("image_decode");
    expect(el.childElementCount).toBe(0);
  });

  it("rejette prepare sans source", async () => {
    const el = make("content.image");
    el.configure({}, ctx);
    await expect(el.prepare()).rejects.toThrow("image_config");
  });
});

describe("content.video", () => {
  const props = { src: "media/v1/original", audio: false, fit: "contain" };

  it("résout prepare sur loadeddata, joue en muet, émet vds-ended", async () => {
    const el = make("content.video");
    const seen = events(el);
    el.configure(props, ctx);
    const ready = el.prepare();
    const video = el.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("src")).toBe("https://srv/espace/affichage/media/v1/original");
    video.dispatchEvent(new Event("loadeddata"));
    await ready;
    el.start();
    expect(played).toEqual([video]);
    expect(video.muted).toBe(true);
    video.dispatchEvent(new Event("ended"));
    expect(seen.ended).toBe(1);
    el.stop();
    expect(video.hasAttribute("src")).toBe(false);
    expect(el.childElementCount).toBe(0);
  });

  it("active le son quand props.audio", async () => {
    const el = make("content.video");
    el.configure({ ...props, audio: true }, ctx);
    const ready = el.prepare();
    el.querySelector("video")?.dispatchEvent(new Event("loadeddata"));
    await ready;
    el.start();
    expect((el.querySelector("video") as HTMLVideoElement).muted).toBe(false);
  });

  it("rejette prepare sur erreur de chargement", async () => {
    const el = make("content.video");
    el.configure(props, ctx);
    const ready = el.prepare();
    el.querySelector("video")?.dispatchEvent(new Event("error"));
    await expect(ready).rejects.toThrow("video_load");
    expect(el.childElementCount).toBe(0);
  });

  it("rejette prepare après le délai de chargement", async () => {
    vi.useFakeTimers();
    const el = make("content.video");
    el.configure(props, ctx);
    const ready = el.prepare();
    const check = expect(ready).rejects.toThrow("video_timeout");
    await vi.advanceTimersByTimeAsync(10_000);
    await check;
  });

  it("remonte vds-error si la lecture échoue après le démarrage", async () => {
    const el = make("content.video");
    const seen = events(el);
    el.configure(props, ctx);
    const ready = el.prepare();
    const video = el.querySelector("video") as HTMLVideoElement;
    video.dispatchEvent(new Event("loadeddata"));
    await ready;
    el.start();
    video.dispatchEvent(new Event("error"));
    expect(seen.errors).toEqual(["video_playback"]);
  });
});

describe("content.pdf", () => {
  const pages = ["media/d1/page-1", "media/d1/page-2", "media/d1/page-3"];

  it("lit les props résolues par le serveur (pages, page_count)", async () => {
    const el = make("content.pdf");
    el.configure({ pages, page_count: 3, page_duration_ms: 2000 }, ctx);
    await el.prepare();
    expect(el.querySelector("img")?.getAttribute("src")).toBe("https://srv/espace/affichage/media/d1/page-1");
  });

  it("rejette prepare sans page", async () => {
    const el = make("content.pdf");
    el.configure({ pages: [], page_duration_ms: 2000 }, ctx);
    await expect(el.prepare()).rejects.toThrow("pdf_no_pages");
  });

  it("pagine à page_duration_ms puis émet vds-ended après la dernière page", async () => {
    vi.useFakeTimers();
    const el = make("content.pdf");
    const seen = events(el);
    el.configure({ pages, page_duration_ms: 2000 }, ctx);
    await el.prepare();
    const shown = () => el.querySelector("img")?.getAttribute("src");
    expect(shown()).toBe("https://srv/espace/affichage/media/d1/page-1");
    el.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(shown()).toBe("https://srv/espace/affichage/media/d1/page-2");
    await vi.advanceTimersByTimeAsync(2000);
    expect(shown()).toBe("https://srv/espace/affichage/media/d1/page-3");
    expect(seen.ended).toBe(0);
    await vi.advanceTimersByTimeAsync(2000);
    expect(seen.ended).toBe(1);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(seen.ended).toBe(1);
  });

  it("émet vds-error si une page suivante est illisible", async () => {
    vi.useFakeTimers();
    const el = make("content.pdf");
    const seen = events(el);
    el.configure({ pages, page_duration_ms: 1000 }, ctx);
    let calls = 0;
    decodeResult = () => (calls++ === 0 ? Promise.resolve() : Promise.reject(new Error("x")));
    await el.prepare();
    el.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(seen.errors).toEqual(["pdf_page"]);
  });

  it("stop() arrête la pagination", async () => {
    vi.useFakeTimers();
    const el = make("content.pdf");
    const seen = events(el);
    el.configure({ pages: pages.slice(0, 1), page_duration_ms: 1000 }, ctx);
    await el.prepare();
    el.start();
    el.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(seen.ended).toBe(0);
    expect(el.childElementCount).toBe(0);
  });
});

describe("content.url", () => {
  const url = "https://example.org/page";

  it("isole la page dans une iframe sandbox sans navigation du haut", async () => {
    const el = make("content.url");
    el.configure({ url, reload_interval_s: 0 }, ctx);
    const ready = el.prepare();
    const frame = el.querySelector("iframe") as HTMLIFrameElement;
    frame.dispatchEvent(new Event("load"));
    await ready;
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox.split(" ").sort()).toEqual(["allow-same-origin", "allow-scripts"]);
    expect(frame.getAttribute("src")).toBe(url);
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    el.start();
    el.stop();
    expect(el.childElementCount).toBe(0);
    expect(frame.getAttribute("src")).toBe("about:blank");
  });

  it.each(["http://example.org", "javascript:alert(1)", "", "not a url"])(
    "refuse l'adresse %j",
    async (bad) => {
      const el = make("content.url");
      el.configure({ url: bad }, ctx);
      await expect(el.prepare()).rejects.toThrow("url_invalid");
      expect(el.querySelector("iframe")).toBeNull();
    }
  );

  it.each([
    `${window.location.origin}/espace/affichage/admin`,
    `${window.location.origin}/`,
  ])("refuse l'adresse de même origine %j", async (same) => {
    const el = make("content.url");
    el.configure({ url: same }, ctx);
    await expect(el.prepare()).rejects.toThrow("url_same_origin");
    expect(el.querySelector("iframe")).toBeNull();
  });

  it("est sauté hors ligne", async () => {
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const el = make("content.url");
    el.configure({ url }, ctx);
    await expect(el.prepare()).rejects.toThrow("offline");
    expect(el.querySelector("iframe")).toBeNull();
    online.mockRestore();
  });

  it("rejette prepare si la page ne charge pas", async () => {
    vi.useFakeTimers();
    const el = make("content.url");
    el.configure({ url }, ctx);
    const check = expect(el.prepare()).rejects.toThrow("url_timeout");
    await vi.advanceTimersByTimeAsync(10_000);
    await check;
    expect(el.childElementCount).toBe(0);
  });

  it("recharge à intervalle régulier et signale la perte de réseau", async () => {
    vi.useFakeTimers();
    const el = make("content.url");
    const seen = events(el);
    el.configure({ url, reload_interval_s: 30 }, ctx);
    const ready = el.prepare();
    const frame = el.querySelector("iframe") as HTMLIFrameElement;
    frame.dispatchEvent(new Event("load"));
    await ready;
    el.start();
    frame.src = "https://example.org/navigated";
    await vi.advanceTimersByTimeAsync(30_000);
    expect(frame.getAttribute("src")).toBe(url);
    window.dispatchEvent(new Event("offline"));
    expect(seen.errors).toEqual(["offline"]);
    el.stop();
    window.dispatchEvent(new Event("offline"));
    expect(seen.errors).toEqual(["offline"]);
  });
});
