import { getPlatformBridge } from "@vds/sdk-ts";
import type { KioskEnforcement, KioskMode, KioskRequest } from "@vds/sdk-ts";

/** Durée maximale d'une maintenance (min) : bornée aussi côté player, par rapport à l'heure serveur du document. */
export const MAX_MAINTENANCE_MIN = 240;

let webLocked = true;

/** Kiosque dans la page : clavier, souris, tactile, navigation, sélection neutralisés tant que le mode est `locked`. */
export function lockDown(): void {
  const block = (e: Event): void => {
    if (!webLocked) return;
    e.preventDefault();
    e.stopPropagation();
  };
  for (const type of ["contextmenu", "dragstart", "selectstart", "keydown", "keypress", "keyup", "wheel", "auxclick", "dblclick"]) {
    window.addEventListener(type, block, { capture: true, passive: false });
  }
  // Touche Retour / geste précédent : on reste sur la page (verrouillé seulement).
  history.pushState(null, "", location.href);
  window.addEventListener("popstate", () => {
    if (webLocked) history.pushState(null, "", location.href);
  });
  window.addEventListener("beforeunload", () => undefined);
}

export interface KioskReport {
  mode: KioskMode;
  enforcement: KioskEnforcement;
}

export interface KioskDeps {
  /** Horloge corrigée de la dérive serveur. */
  now: () => number;
}

/**
 * Applique le mode kiosque servi (jamais décidé ici). La demande vit dans la programmation persistée
 * (IndexedDB) : `unlocked` et une maintenance non expirée survivent à un redémarrage hors ligne.
 * Reverrouillage autonome à `until`, vérifié à chaque tick du moteur.
 */
export class KioskController {
  private mode: KioskMode = "locked";
  private until = 0;
  private enforcement: KioskEnforcement = "web";
  private sent = "";

  constructor(private readonly deps: KioskDeps) {}

  /** Nouvelle demande (programmation activée ou rechargée). Absent ou invalide = `locked`. */
  apply(req: KioskRequest | undefined, serverTime?: string): void {
    const now = this.deps.now();
    let mode: KioskMode = "locked";
    let until = 0;
    if (req?.mode === "unlocked") mode = "unlocked";
    else if (req?.mode === "maintenance") {
      const u = Date.parse(req.until ?? "");
      const base = Date.parse(serverTime ?? "");
      const cap = (Number.isNaN(base) ? now : base) + MAX_MAINTENANCE_MIN * 60_000;
      until = Math.min(u, cap);
      if (Number.isFinite(until) && until > now) mode = "maintenance";
      else until = 0;
    }
    this.mode = mode;
    this.until = until;
    this.push();
  }

  /** À appeler à chaque tick : reverrouille dès l'échéance, même sans serveur. */
  tick(): void {
    if (this.mode === "maintenance" && this.deps.now() >= this.until) {
      this.mode = "locked";
      this.until = 0;
      this.push();
    } else if (this.sent === "" && getPlatformBridge().kiosk) this.push(); // envoi précédent échoué : nouvel essai
  }

  get locked(): boolean {
    return this.mode === "locked";
  }

  report(): KioskReport {
    return { mode: this.mode, enforcement: this.enforcement };
  }

  private push(): void {
    webLocked = this.mode === "locked";
    const remaining = this.mode === "maintenance" ? Math.max(0, this.until - this.deps.now()) : 0;
    // Le natif borne et reverrouille lui-même ; on ne renvoie le message que s'il change (la durée restante, jamais).
    const key = `${this.mode}:${this.until}`;
    const bridge = getPlatformBridge();
    if (!bridge.kiosk || key === this.sent) return;
    this.sent = key;
    const mode = this.mode;
    void bridge
      .kiosk(mode, Math.round(remaining))
      .then((raw) => {
        const r = JSON.parse(raw) as { enforcement?: string };
        this.enforcement = r.enforcement === "lock_task" || r.enforcement === "soft" ? r.enforcement : "web";
      })
      .catch(() => {
        this.sent = "";
      });
  }
}
