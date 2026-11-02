// vds-content-url : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
// requires_network = true : le player saute l'élément hors ligne ; on se protège aussi ici.
const LOAD_TIMEOUT_MS = 9000;
// Bail natif : le module renouvelle `rect` toutes les 10 s (le natif détruit la vue après 30 s sans nouvelle).
const RECT_RENEW_MS = 10000;
let nextViewId = 0;

// Chemin natif (Android, architecture-content-url-natif.md) si le shell expose VdsNative.web ; sinon iframe.
function nativeWeb() {
  const web = typeof window !== "undefined" ? window.VdsNative?.web : undefined;
  return web && typeof web.prepare === "function" ? web : null;
}

function isHttps(value) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

// Même origine que le player (quel que soit VDS_BASE_PATH) : refusée, jamais chargée en iframe.
function isSameOrigin(value) {
  try {
    return new URL(value).origin === window.location.origin;
  } catch {
    return false; // illisible : refusé ensuite par isHttps
  }
}

export class VdsContentUrl extends HTMLElement {
  #url = "";
  #reloadMs = 0;
  #frame = null;
  #timer = null;
  #onOffline = null;
  #viewId = null; // non nul = vue native active
  #observer = null;
  #rectTimer = null;

  configure(props) {
    this.#url = props && typeof props.url === "string" ? props.url : "";
    const s = Number(props?.reload_interval_s);
    this.#reloadMs = Number.isFinite(s) && s > 0 ? s * 1000 : 0;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden";
  }

  prepare() {
    if (isSameOrigin(this.#url)) return Promise.reject(new Error("url_same_origin"));
    if (!isHttps(this.#url)) return Promise.reject(new Error("url_invalid"));
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      return Promise.reject(new Error("offline"));
    }
    const web = nativeWeb();
    if (web) return this.#prepareNative(web);
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("allow", "");
    frame.setAttribute("tabindex", "-1");
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;background:#000";
    this.#frame = frame;
    return new Promise((resolve, reject) => {
      const settle = (fn, arg) => {
        clearTimeout(timer);
        frame.removeEventListener("load", onLoad);
        fn(arg);
      };
      const onLoad = () => settle(resolve);
      const timer = setTimeout(() => {
        this.stop();
        settle(reject, new Error("url_timeout"));
      }, LOAD_TIMEOUT_MS);
      frame.addEventListener("load", onLoad, { once: true });
      this.replaceChildren(frame);
      frame.src = this.#url;
    });
  }

  #rect() {
    const r = this.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }

  async #prepareNative(web) {
    this.#viewId ??= `u${++nextViewId}`;
    const id = this.#viewId;
    let timer;
    const guard = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("url_timeout")), LOAD_TIMEOUT_MS + 1000);
    });
    try {
      const raw = await Promise.race([web.prepare(id, this.#url, this.#rect()), guard]);
      let res;
      try {
        res = JSON.parse(raw);
      } catch {
        res = null;
      }
      if (!res || res.ok !== true) {
        throw new Error(typeof res?.code === "string" && res.code ? res.code : "url_native_error");
      }
    } catch (e) {
      this.#releaseNative();
      throw e instanceof Error ? e : new Error("url_native_error");
    } finally {
      clearTimeout(timer);
    }
  }

  #releaseNative() {
    const id = this.#viewId;
    this.#viewId = null;
    this.#observer?.disconnect();
    this.#observer = null;
    clearInterval(this.#rectTimer);
    this.#rectTimer = null;
    if (id) {
      try {
        nativeWeb()?.hide(id);
      } catch {
        // pont indisponible : rien à nettoyer côté JS
      }
    }
  }

  #startNative(web) {
    const id = this.#viewId;
    const send = () => {
      if (this.#viewId === id) web.rect(id, this.#rect());
    };
    web.show(id);
    if (typeof ResizeObserver !== "undefined") {
      this.#observer = new ResizeObserver(send);
      this.#observer.observe(this);
    }
    this.#rectTimer = setInterval(send, RECT_RENEW_MS);
  }

  start() {
    this.style.visibility = "visible";
    const web = this.#viewId ? nativeWeb() : null;
    if (web) this.#startNative(web);
    this.#onOffline = () =>
      this.dispatchEvent(new CustomEvent("vds-error", { detail: { code: "offline" } }));
    window.addEventListener("offline", this.#onOffline);
    if (this.#reloadMs > 0) {
      this.#timer = setInterval(() => {
        if (this.#frame) this.#frame.src = this.#url;
        else if (this.#viewId) this.#reloadNative();
      }, this.#reloadMs);
    }
  }

  #reloadNative() {
    const web = nativeWeb();
    if (!web || !this.#viewId) return;
    this.#prepareNative(web).then(
      () => undefined,
      (e) => this.dispatchEvent(new CustomEvent("vds-error", { detail: { code: e.message } })),
    );
  }

  stop() {
    this.#releaseNative();
    this.style.visibility = "hidden";
    clearInterval(this.#timer);
    this.#timer = null;
    if (this.#onOffline) window.removeEventListener("offline", this.#onOffline);
    this.#onOffline = null;
    if (this.#frame) this.#frame.src = "about:blank";
    this.#frame = null;
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-url")) {
  customElements.define("vds-content-url", VdsContentUrl);
}
