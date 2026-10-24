import type { KioskEnforcement, KioskMode, KioskRequest, PlatformBridge } from "@vds/sdk-ts";

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
  /** Mode APPLIQUÉ rapporté par le natif (hors natif : le mode servi). Jamais la seule demande JS. */
  mode: KioskMode;
  enforcement: KioskEnforcement;
}

/** Fonction d'envoi au natif, remise une seule fois à ce module (jeton du pont inclus). */
export type KioskSender = NonNullable<ReturnType<NonNullable<PlatformBridge["claimKiosk"]>>>;

let claimed: KioskSender | undefined;

/**
 * À appeler tout au début du démarrage, avant tout content-type : réclame l'unique accès au pont kiosque
 * (le natif ne le remet qu'une fois) puis retire `claimKiosk` de `window.VdsNative`. Un module tiers chargé
 * ensuite ne peut ni déverrouiller ni obtenir le jeton.
 */
export function claimKioskBridge(): void {
  const native = window.VdsNative;
  if (!native?.claimKiosk) return;
  claimed ??= native.claimKiosk() ?? undefined;
  delete native.claimKiosk;
}

/** Écart toléré entre l'horloge corrigée et l'heure serveur du document avant de la juger manipulée. */
export const CLOCK_REWIND_TOLERANCE_MS = 5 * 60_000;
/** Un déverrouillage persistant est un bail natif : renouvelé tant que le player vit. */
export const UNLOCK_RENEW_MS = 5 * 60_000;
/** Délai minimal entre deux envois quand le natif n'a pas confirmé l'état demandé. */
export const RETRY_MS = 5_000;

export interface KioskDeps {
  /** Horloge corrigée de la dérive serveur. */
  now: () => number;
  /** Horloge monotone (renouvellement et nouvelles tentatives) ; défaut `performance.now`. */
  mono?: () => number;
  /** Envoi au natif ; défaut : l'accès réclamé par [claimKioskBridge]. */
  send?: () => KioskSender | undefined;
}

/**
 * Applique le mode kiosque servi (jamais décidé ici). La demande vit dans la programmation persistée
 * (IndexedDB) : `unlocked` et une maintenance non expirée survivent à un redémarrage hors ligne.
 * Reverrouillage autonome à `until`, vérifié à chaque tick du moteur. Tant que le natif n'a pas confirmé
 * l'état demandé (`ok:false`, échec de verrouillage), la demande est renvoyée (au plus toutes les 5 s).
 */
export class KioskController {
  private mode: KioskMode = "locked";
  private until = 0;
  private enforcement: KioskEnforcement = "web";
  private applied: KioskMode | null = null;
  private confirmedKey = "";
  private lastKey = "";
  private inflight = false;
  private lastSent = Number.NEGATIVE_INFINITY;
  private serverBase = Number.NaN;

  constructor(private readonly deps: KioskDeps) {}

  private sender(): KioskSender | undefined {
    return (this.deps.send ?? (() => claimed))();
  }

  private mono(): number {
    return (this.deps.mono ?? (() => performance.now()))();
  }

  /** Nouvelle demande (programmation activée ou rechargée). Absent ou invalide = `locked`. */
  apply(req: KioskRequest | undefined, serverTime?: string): void {
    const now = this.deps.now();
    this.serverBase = Date.parse(serverTime ?? "");
    let mode: KioskMode = "locked";
    let until = 0;
    if (req?.mode === "unlocked") mode = "unlocked";
    else if (req?.mode === "maintenance") {
      const u = Date.parse(req.until ?? "");
      const base = this.serverBase;
      const cap = (Number.isNaN(base) ? now : base) + MAX_MAINTENANCE_MIN * 60_000;
      until = Math.min(u, cap);
      // Horloge locale en retard sur l'heure serveur du document : manipulée, jamais de maintenance.
      const rewound = !Number.isNaN(base) && now < base - CLOCK_REWIND_TOLERANCE_MS;
      if (Number.isFinite(until) && until > now && !rewound) mode = "maintenance";
      else until = 0;
    }
    this.mode = mode;
    this.until = until;
    this.push();
  }

  /** À appeler à chaque tick : reverrouille dès l'échéance, même sans serveur ; réessaie si non confirmé. */
  tick(): void {
    if (this.mode === "maintenance") {
      const now = this.deps.now();
      const rewound = !Number.isNaN(this.serverBase) && now < this.serverBase - CLOCK_REWIND_TOLERANCE_MS;
      if (now >= this.until || rewound) {
        this.mode = "locked";
        this.until = 0;
      }
    }
    this.push();
  }

  get locked(): boolean {
    return this.mode === "locked";
  }

  report(): KioskReport {
    const native = this.sender() !== undefined;
    return { mode: native ? (this.applied ?? "locked") : this.mode, enforcement: this.enforcement };
  }

  private push(): void {
    webLocked = this.mode === "locked";
    const send = this.sender();
    if (!send || this.inflight) return;
    const key = `${this.mode}:${this.until}`;
    const since = this.mono() - this.lastSent;
    const confirmed = key === this.confirmedKey;
    const due = this.mode === "unlocked" ? since >= UNLOCK_RENEW_MS : false;
    const retry = !confirmed && since >= RETRY_MS;
    // Premier envoi d'un nouvel état : immédiat ; sinon seulement renouvellement ou nouvelle tentative.
    if (key === this.lastKey && !due && !retry) return;
    this.lastKey = key;
    this.lastSent = this.mono();
    this.inflight = true;
    const remaining = this.mode === "maintenance" ? Math.max(0, this.until - this.deps.now()) : 0;
    const window = this.mode === "maintenance" ? Math.floor(this.until / 1000) : 0;
    const asked = this.mode;
    void send(asked, Math.round(remaining), window)
      .then((raw) => {
        const r = JSON.parse(raw) as { ok?: boolean; enforcement?: string; mode?: string };
        const m = r.mode;
        this.applied = m === "locked" || m === "unlocked" || m === "maintenance" ? m : null;
        this.enforcement = r.enforcement === "lock_task" ? "lock_task" : "soft";
        // Confirmé = le natif dit ok ET applique bien ce qui est demandé.
        this.confirmedKey = r.ok === true && this.applied === asked && key === `${this.mode}:${this.until}` ? key : "";
      })
      .catch(() => {
        this.confirmedKey = "";
      })
      .finally(() => {
        this.inflight = false;
      });
  }

}
