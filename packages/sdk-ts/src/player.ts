export const SCHEDULE_FORMAT = 1;

export interface ScheduleDocument {
  format: typeof SCHEDULE_FORMAT;
  revision: string;
  server_time: string;
  screen: {
    id: string;
    name: string;
    orientation: "landscape" | "portrait";
  };
  timezone: string;
  valid_from: string;
  valid_until: string;
  slots: ScheduleSlot[];
  playlists: Record<string, Playlist>;
  assets: Record<string, AssetRef>;
  content_types: Record<string, ContentTypeEntry>;
  /** Plan d'alimentation résolu (J et J+1), exécuté par le player même hors ligne. Absent = toujours allumé. */
  power?: PowerSlot[];
  /** Kiosque demandé (champ additif). Absent = `locked`. Le player applique, ne décide jamais. */
  kiosk?: KioskRequest;
  /** 1.9.0 : limites de lecture servies par le serveur. Absent = défauts (1 élément exclusif à la fois). */
  player?: { max_exclusive_elements: number };
  /** 1.11.0 : capture périodique. Absent = désactivée. */
  screenshot?: { interval_min: number };
}

export type KioskMode = "locked" | "unlocked" | "maintenance";

export interface KioskRequest {
  mode: KioskMode;
  /** Échéance ISO de la maintenance ; le player reverrouille seul à cette heure. */
  until?: string | null;
}

/** Application effective du kiosque (heartbeat). */
export type KioskEnforcement = "lock_task" | "soft" | "web";

/** Créneau d'alimentation : ISO 8601 avec décalage, `from` inclus, `to` exclu. */
export interface PowerSlot {
  from: string;
  to: string;
  state: "on" | "off";
}

export interface OffSlot {
  start: string;
  end: string;
  kind: "off";
  mode: "black";
  /** vds-core 1.7.0 : présent si l'extinction est forcée (absent = hors plage). */
  override_id?: string | null;
}

export interface PlaySlot {
  start: string;
  end: string;
  kind: "play";
  layout: {
    /** Triées par z croissant puis id (un player 1.8 empile dans l'ordre du tableau). */
    zones: LayoutZone[];
    /** vds-core 1.9.0 : null = mise en page implicite (cible playlist). */
    id?: string | null;
    revision?: number;
    orientation?: "landscape" | "portrait";
    /** `asset` = clé de `assets`. */
    background?: { color?: string; asset?: string } | null;
  };
  source: {
    /** null pour un forçage (vds-core 1.7.0). */
    schedule_id: string | null;
    kind?: "schedule" | "override";
    override_id?: string | null;
    /** vds-core 1.9.0 */
    layout_id?: string | null;
  };
}

export type ScheduleSlot = OffSlot | PlaySlot;

export interface LayoutZone {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  playlist: string;
  /** vds-core 1.9.0 (tous optionnels, ignorés par un player 1.8) : */
  z?: number;
  fit?: "contain" | "cover" | "stretch";
  /** ISO UTC : la zone apparaît à cet instant. */
  from?: string | null;
  /** ISO UTC : la zone disparaît à cet instant. */
  until?: string | null;
  /** Forçages de zone, triés par from, sans chevauchement, bornés au créneau. */
  overrides?: ZoneOverrideWindow[];
}

/** vds-core 1.9.0 */
export interface ZoneOverrideWindow {
  override_id: string;
  /** Clé de `playlists`. */
  playlist: string;
  from: string;
  until: string;
}

export interface Playlist {
  items: PlaylistItem[];
}

export interface PlaylistItem {
  id: string;
  content_type: string;
  duration_ms: number | null;
  props: Record<string, unknown>;
  assets: string[];
  requires_network: boolean;
  /** vds-core 1.8.0 : instant UTC (ISO 8601) à partir duquel l'élément est sauté (média expiré). */
  until?: string | null;
  /** vds-core 1.12.0 : identifiants de flux de données à précharger (absent = aucun). */
  feeds?: string[];
}

export interface AssetRef {
  sha256: string;
  bytes: number;
  mime: string;
}

export interface ContentTypeEntry {
  version: string;
  element: string;
  script: string;
}

/** Rectangle en px CSS ; le natif multiplie par la densité. */
export interface VdsNativeRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Vues web natives (Android, optionnel) pour `content.url` : voir docs/architecture-content-url-natif.md.
 * `id` : `[A-Za-z0-9_-]{1,32}`, au plus 2 vues à la fois.
 */
export interface VdsNativeWeb {
  /** Crée ou recharge la vue `id` cachée ; résout un JSON `{ok:boolean, code?:string}` une fois la page chargée sans erreur. */
  prepare(id: string, url: string, rect: VdsNativeRect): Promise<string>;
  show(id: string): void;
  rect(id: string, rect: VdsNativeRect): void;
  /** about:blank puis retire la vue. */
  hide(id: string): void;
}

/**
 * Commandes player natives (Android), remises UNE seule fois au contrôleur de commandes du player.
 * Chaque appel résout un JSON `{ok, code?}` (+ `jpeg` base64 pour `shot`, + `screenshot`/`restart_app`/`restart_device`
 * pour `caps`). Rien de technique n'est affiché : les codes servent au résultat de commande uniquement.
 */
export interface NativeCmd {
  caps(): Promise<string>;
  shot(maxEdge: number, quality: number): Promise<string>;
  restart(scope: "app" | "device"): Promise<string>;
}

export interface PlatformBridge {
  /** Absent hors Android ou sur un APK trop ancien : `content.url` retombe sur l'iframe. */
  web?: VdsNativeWeb;
  ping(): void;
  info(): { platform: string; model: string; app_version: string; os_version: string; insecure_upstream?: boolean; app_version_code?: number };
  reload(): void;
  /**
   * Pont CEC (Android) : `action` = probe | on | off | state. Résout un JSON
   * `{ok, state, verified, code?}`. Absent hors Android ou sans CEC.
   */
  cec?(action: "probe" | "on" | "off" | "state"): Promise<string>;
  /**
   * Kiosque natif (Android) : accès remis UNE seule fois (puis `null`) au contrôleur kiosque du player,
   * avant tout content-type ; réservé à lui (le jeton est dans la fermeture). La fonction reçoit
   * `remainingMs` (durée restante de la maintenance, horloge du player, 0 sinon) et `windowSec` (échéance
   * `until` en secondes, 0 sinon : identifie la fenêtre, le natif ne la réarme jamais) et résout un JSON
   * `{ok, enforcement, mode}` : `mode` = mode APPLIQUÉ, `ok:false` = état demandé non confirmé.
   */
  /**
   * Ticket d'appairage injecté à l'installation (Android, ADB) : résout un JSON `{"ticket":"…"}` ou `{}`.
   * Le natif ne le remet qu'une fois (puis l'efface) ; il ne doit jamais être persisté ni affiché.
   */
  claimPairingTicket?(): Promise<string>;
  /** Commandes player natives : remises une fois (puis `null`), voir [NativeCmd]. */
  claimCmd?(): NativeCmd | null;
  claimKiosk?(): ((mode: KioskMode, remainingMs: number, windowSec: number) => Promise<string>) | null;
}

declare global {
  interface Window {
    VdsNative?: PlatformBridge;
  }
}

/**
 * Retourne le pont plateforme si disponible, sinon une implémentation web sans effet.
 */
export function getPlatformBridge(): PlatformBridge {
  if (window.VdsNative) {
    return window.VdsNative;
  }

  // Implémentation web de secours
  return {
    ping: () => {},
    info: () => ({
      platform: "web",
      model: "",
      app_version: "",
      os_version: ""
    }),
    reload: () => {}
  };
}
