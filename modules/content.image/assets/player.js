// vds-content-image : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
const FITS = ["contain", "cover"];

function decodeImage(img) {
  if (typeof img.decode === "function") return img.decode();
  return new Promise((resolve, reject) => {
    img.addEventListener("load", () => resolve(), { once: true });
    img.addEventListener("error", () => reject(new Error("decode")), { once: true });
  });
}

export class VdsContentImage extends HTMLElement {
  #props = null;
  #ctx = null;
  #img = null;

  configure(props, ctx) {
    // `media` : clé de l'item.schema.json (résolue en ressource par le serveur) ; `src` : ancien nom toléré.
    const ref = props?.media ?? props?.src;
    const src = typeof ref === "string" ? ref : "";
    this.#props = { src, fit: FITS.includes(props?.fit) ? props.fit : "contain" };
    this.#ctx = ctx;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden";
  }

  async prepare() {
    if (!this.#props || !this.#props.src || !this.#ctx) throw new Error("image_config");
    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.style.cssText = `width:100%;height:100%;object-fit:${this.#props.fit};display:block`;
    img.src = this.#ctx.resolveUrl(this.#props.src);
    this.#img = img;
    try {
      await decodeImage(img);
    } catch {
      this.stop();
      throw new Error("image_decode");
    }
    this.replaceChildren(img);
  }

  start() {
    this.style.visibility = "visible";
  }

  stop() {
    this.style.visibility = "hidden";
    if (this.#img) {
      this.#img.removeAttribute("src");
      this.#img = null;
    }
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-image")) {
  customElements.define("vds-content-image", VdsContentImage);
}
