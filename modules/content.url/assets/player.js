// vds-content-url : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
// requires_network = true : le player saute l'élément hors ligne ; on se protège aussi ici.
const LOAD_TIMEOUT_MS = 9000;

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

  start() {
    this.style.visibility = "visible";
    this.#onOffline = () =>
      this.dispatchEvent(new CustomEvent("vds-error", { detail: { code: "offline" } }));
    window.addEventListener("offline", this.#onOffline);
    if (this.#reloadMs > 0) {
      this.#timer = setInterval(() => {
        if (this.#frame) this.#frame.src = this.#url;
      }, this.#reloadMs);
    }
  }

  stop() {
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
