// vds-content-pdf : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
const DEFAULT_PAGE_MS = 8000;

function decodeImage(img) {
  if (typeof img.decode === "function") return img.decode();
  return new Promise((resolve, reject) => {
    img.addEventListener("load", () => resolve(), { once: true });
    img.addEventListener("error", () => reject(new Error("decode")), { once: true });
  });
}

export class VdsContentPdf extends HTMLElement {
  #pages = [];
  #pageMs = DEFAULT_PAGE_MS;
  #ctx = null;
  #current = null;
  #next = null;
  #index = 0;
  #timer = null;
  #running = false;

  configure(props, ctx) {
    const pages = Array.isArray(props?.pages) ? props.pages.filter((p) => typeof p === "string") : [];
    const ms = Number(props?.page_duration_ms);
    this.#pages = pages;
    this.#pageMs = Number.isFinite(ms) && ms >= 1000 ? ms : DEFAULT_PAGE_MS;
    this.#ctx = ctx;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden";
  }

  async #load(index) {
    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.style.cssText = "width:100%;height:100%;object-fit:contain;display:block";
    img.src = this.#ctx.resolveUrl(this.#pages[index]);
    await decodeImage(img);
    return img;
  }

  async prepare() {
    if (!this.#ctx || this.#pages.length === 0) throw new Error("pdf_no_pages");
    try {
      this.#current = await this.#load(0);
    } catch {
      this.stop();
      throw new Error("pdf_page");
    }
    this.#index = 0;
    this.replaceChildren(this.#current);
    this.#preloadNext();
  }

  #preloadNext() {
    const next = this.#index + 1;
    this.#next = next < this.#pages.length ? this.#load(next) : null;
    this.#next?.catch(() => {});
  }

  start() {
    this.style.visibility = "visible";
    this.#running = true;
    this.#schedule();
  }

  #schedule() {
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => this.#advance(), this.#pageMs);
  }

  async #advance() {
    if (!this.#running) return;
    if (!this.#next) {
      this.#running = false;
      this.dispatchEvent(new Event("vds-ended"));
      return;
    }
    let img;
    try {
      img = await this.#next;
    } catch {
      if (!this.#running) return;
      this.#running = false;
      this.dispatchEvent(new CustomEvent("vds-error", { detail: { code: "pdf_page" } }));
      return;
    }
    if (!this.#running) return;
    this.#current?.removeAttribute("src");
    this.#current = img;
    this.#index += 1;
    this.replaceChildren(img);
    this.#preloadNext();
    this.#schedule();
  }

  stop() {
    this.#running = false;
    clearTimeout(this.#timer);
    this.#timer = null;
    this.style.visibility = "hidden";
    this.#current?.removeAttribute("src");
    this.#current = null;
    this.#next = null;
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-pdf")) {
  customElements.define("vds-content-pdf", VdsContentPdf);
}
