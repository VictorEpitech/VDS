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
}

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
}

export interface PlaySlot {
  start: string;
  end: string;
  kind: "play";
  layout: {
    zones: LayoutZone[];
  };
  source: {
    schedule_id: string;
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

export interface PlatformBridge {
  ping(): void;
  info(): { platform: string; model: string; app_version: string; os_version: string; insecure_upstream?: boolean };
  reload(): void;
  /**
   * Pont CEC (Android) : `action` = probe | on | off | state. Résout un JSON
   * `{ok, state, verified, code?}`. Absent hors Android ou sans CEC.
   */
  cec?(action: "probe" | "on" | "off" | "state"): Promise<string>;
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
