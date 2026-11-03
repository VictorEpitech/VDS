// vds-content-rss : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
// Données : flux RSS/Atom déjà réduit en texte par le serveur (`ctx.feed`). Tout est inséré avec `textContent`
// (jamais innerHTML) : aucun balisage du flux ne peut être interprété. Hors ligne : dernier état connu tant qu'il a
// moins de `max_age_s` ; au-delà, ou sans donnée, l'élément échoue (`feed_stale` / `feed_unavailable`) et est sauté.

const TICKER_PX_PER_S = 90;
const ROW_MIN_RATIO = 0.22; // hauteur minimale d'une entrée, en fraction de la plus petite dimension de la zone
const SEPARATOR = "   •   ";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");

/** Entrées valides du flux ; toute forme inattendue = flux inutilisable. */
function parse(data, max) {
  if (!Array.isArray(data?.items)) return null;
  const items = data.items
    .map((i) => ({ title: str(i?.title, 200), summary: str(i?.summary, 400), published: str(i?.published, 40) }))
    .filter((i) => i.title || i.summary)
    .slice(0, max);
  return items.length > 0 ? items : null;
}

function box(tag, css, text) {
  const n = document.createElement(tag);
  n.style.cssText = css;
  if (text) n.textContent = text;
  return n;
}

export class VdsContentRss extends HTMLElement {
  #props = null;
  #ctx = null;
  #items = null;
  #page = null;
  #pages = [];
  #timer = null;
  #anim = null;

  configure(props, ctx) {
    this.#props = {
      display: props?.display === "ticker" ? "ticker" : "list",
      maxItems: Number.isInteger(props?.max_items) ? Math.min(10, Math.max(1, props.max_items)) : 5,
      stepMs: (isNum(props?.item_duration_s) ? Math.min(120, Math.max(2, props.item_duration_s)) : 8) * 1000,
      showDate: props?.show_date !== false,
      maxAgeMs: (isNum(props?.max_age_s) && props.max_age_s > 0 ? props.max_age_s : 86400) * 1000,
      feedId: typeof props?.feed_id === "string" ? props.feed_id : "",
    };
    this.#ctx = ctx;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden;overflow:hidden";
  }

  async prepare() {
    const p = this.#props;
    const ctx = this.#ctx;
    if (!p || !ctx || !p.feedId || typeof ctx.feed !== "function") throw new Error("feed_unavailable");
    const snap = await ctx.feed(p.feedId);
    const items = snap ? parse(snap.data, p.maxItems) : null;
    if (!snap || !items) throw new Error("feed_unavailable");
    const fetched = Date.parse(snap.fetchedAt);
    const nowMs = typeof ctx.now === "function" ? ctx.now() : Date.now();
    if (!Number.isFinite(fetched) || nowMs - fetched > p.maxAgeMs) throw new Error("feed_stale");
    this.#items = items;
    if (p.display === "ticker") this.#buildTicker(items);
    else this.#buildList(items);
  }

  #dims() {
    const w = this.clientWidth || this.#ctx.zone.width || 1280;
    const h = this.clientHeight || this.#ctx.zone.height || 720;
    return { w, h, unit: Math.min(w, h) };
  }

  #date(iso) {
    const t = Date.parse(iso);
    if (!this.#props.showDate || !Number.isFinite(t)) return "";
    try {
      return new Intl.DateTimeFormat(this.#ctx.locale, { dateStyle: "medium" }).format(new Date(t));
    } catch {
      return "";
    }
  }

  #frame(css) {
    return box(
      "div",
      `box-sizing:border-box;width:100%;height:100%;overflow:hidden;background:var(--vds-surface, black);color:var(--vds-on-surface, white);font-family:var(--vds-font-body, sans-serif);${css}`,
    );
  }

  #buildList(items) {
    const { w, h, unit } = this.#dims();
    const pad = unit * 0.05;
    const perPage = Math.max(1, Math.min(items.length, Math.floor(h / (unit * ROW_MIN_RATIO + pad))));
    const pageH = h - pad * 2;
    const rowH = pageH / perPage;
    const title = Math.min(rowH * 0.3, w * 0.05, unit * 0.075);
    this.#pages = [];
    for (let i = 0; i < items.length; i += perPage) {
      const page = this.#frame(`display:flex;flex-direction:column;justify-content:space-around;padding:${pad}px;gap:${pad * 0.5}px`);
      for (const it of items.slice(i, i + perPage)) {
        const row = box("div", `min-height:0;overflow:hidden;max-height:${rowH}px`);
        row.append(box("div", `font-size:${title}px;font-weight:700;line-height:1.15`, it.title));
        const d = this.#date(it.published);
        if (d) row.append(box("div", `font-size:${title * 0.55}px;color:var(--vds-on-surface-variant, white);margin-top:${title * 0.1}px`, d));
        if (it.summary) {
          const lines = Math.max(1, Math.floor((rowH - title * 1.4) / (title * 0.62 * 1.3)));
          row.append(
            box(
              "div",
              `font-size:${title * 0.62}px;line-height:1.3;margin-top:${title * 0.2}px;color:var(--vds-on-surface-variant, white);overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:${lines}`,
              it.summary,
            ),
          );
        }
        page.append(row);
      }
      this.#pages.push(page);
    }
    this.replaceChildren(this.#pages[0]);
  }

  #buildTicker(items) {
    const { h } = this.#dims();
    const size = Math.max(14, Math.min(h * 0.45, 96));
    const frame = this.#frame(`display:flex;align-items:center;white-space:nowrap`);
    const line = box("div", `font-size:${size}px;font-weight:600;will-change:transform;flex:none`, items.map((i) => i.title || i.summary).join(SEPARATOR));
    frame.append(line);
    this.#page = line;
    this.replaceChildren(frame);
  }

  start() {
    this.style.visibility = "visible";
    const p = this.#props;
    if (!p || !this.#items) return;
    if (p.display === "ticker") this.#startTicker();
    else this.#startPages();
  }

  #startPages() {
    if (this.#pages.length < 2) return;
    let i = 0;
    const per = Math.ceil(this.#items.length / this.#pages.length);
    this.#timer = setInterval(() => {
      i = (i + 1) % this.#pages.length;
      this.replaceChildren(this.#pages[i]);
    }, this.#props.stepMs * per);
  }

  #startTicker() {
    const line = this.#page;
    const { w } = this.#dims();
    const total = line.scrollWidth + w;
    const ms = Math.max(this.#props.stepMs, (total / TICKER_PX_PER_S) * 1000);
    const done = () => this.dispatchEvent(new Event("vds-ended"));
    if (typeof line.animate === "function") {
      this.#anim = line.animate([{ transform: `translateX(${w}px)` }, { transform: `translateX(-${line.scrollWidth}px)` }], { duration: ms, easing: "linear", fill: "forwards" });
      this.#anim.onfinish = done;
    } else {
      this.#timer = setTimeout(done, ms);
    }
  }

  stop() {
    this.style.visibility = "hidden";
    clearInterval(this.#timer);
    clearTimeout(this.#timer);
    this.#timer = null;
    if (this.#anim) {
      this.#anim.onfinish = null;
      this.#anim.cancel();
      this.#anim = null;
    }
    this.#items = null;
    this.#page = null;
    this.#pages = [];
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-rss")) {
  customElements.define("vds-content-rss", VdsContentRss);
}
