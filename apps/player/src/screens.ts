import { t } from "./i18n";

export type SystemScreen = "loading" | "pairing" | "idle" | "black" | "maintenance" | "none";

export interface MaintenanceInfo {
  code: string;
  revision: string;
  online: boolean;
  uptime_s: number;
  cache_bytes: number;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Couche des écrans système, au-dessus de la scène de lecture.
 * Public : jamais d'information technique, hormis le code d'appairage et l'écran de maintenance.
 */
export class Screens {
  readonly stage = el("div", "stage");
  private readonly layer = el("div", "system");
  private current: SystemScreen = "none";

  constructor(root: HTMLElement) {
    this.layer.setAttribute("role", "status");
    root.replaceChildren(this.stage, this.layer);
    this.show("loading");
  }

  get active(): SystemScreen {
    return this.current;
  }

  show(screen: SystemScreen): void {
    if (screen === this.current && screen !== "pairing") return;
    this.current = screen;
    this.layer.className = `system system-${screen}`;
    this.layer.hidden = screen === "none";
    this.layer.replaceChildren();
    this.layer.removeAttribute("aria-label");
    if (screen === "none" || screen === "black") return;
    if (screen === "idle") {
      this.layer.append(this.logo());
      this.layer.setAttribute("aria-label", t("idle.label"));
    } else if (screen === "loading") {
      const spinner = el("div", "spinner");
      spinner.setAttribute("aria-hidden", "true");
      this.layer.append(this.logo(), spinner);
      this.layer.setAttribute("aria-label", t("loading.label"));
    }
  }

  /** Écran d'appairage : logo, code en grand (police mono), consigne. */
  showPairing(code: string): void {
    this.current = "pairing";
    this.layer.className = "system system-pairing";
    this.layer.hidden = false;
    const title = el("h1", "title", t("pairing.title"));
    const label = el("p", "label", t("pairing.code_label"));
    const value = el("p", "code", code);
    value.dataset.testid = "pairing-code";
    const hint = el("p", "hint", t("pairing.instruction"));
    this.layer.replaceChildren(this.logo(), title, label, value, hint);
  }

  showMaintenance(info: MaintenanceInfo): void {
    this.current = "maintenance";
    this.layer.className = "system system-maintenance";
    this.layer.hidden = false;
    const rows: [string, string][] = [
      [t("maintenance.code_label"), info.code],
      [t("maintenance.revision"), info.revision.slice(0, 19)],
      [t("maintenance.network"), t(info.online ? "maintenance.network_up" : "maintenance.network_down")],
      [t("maintenance.uptime"), `${Math.floor(info.uptime_s / 60)} min`],
      [t("maintenance.cache"), `${Math.round(info.cache_bytes / 1048576)} MB`],
    ];
    const list = el("dl", "diag");
    for (const [k, v] of rows) list.append(el("dt", undefined, k), el("dd", "mono", v));
    this.layer.replaceChildren(this.logo(), el("h1", "title", t("maintenance.title")), list);
  }

  private logo(): HTMLElement {
    const logo = el("div", "vds-logo logo");
    logo.setAttribute("aria-hidden", "true");
    return logo;
  }
}
