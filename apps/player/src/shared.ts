/** Constantes partagées entre la page et le Service Worker (aucune dépendance DOM). */
export const DB_NAME = "vds-player";
export const DB_VERSION = 1;
export const STORE_KV = "kv";
export const STORE_SCHEDULE = "schedule";
export const STORE_LOG = "log";
export const LOG_RING = 500;

export const CACHE_MEDIA = "vds-media-v1";
export const CACHE_SHELL_PREFIX = "vds-shell-";

/** En-tête posé par la page pour demander au SW un téléchargement réseau authentifié. */
export const NET_HEADER = "X-Vds-Net";

export const KV = {
  token: "token",
  screenId: "screen_id",
  pairing: "pairing",
  drift: "drift_ms",
  etag: "etag",
  asset: (key: string): string => `asset:${key}`,
} as const;

export const ERR_DEVICE_REVOKED = "errors.device_revoked";
