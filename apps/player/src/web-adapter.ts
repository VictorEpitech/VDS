import type { PlayerAdapter } from "@vds/sdk-ts";

/**
 * Adaptateur web minimal (PWA, Tizen, Chromium kiosque sans pont) : rechargement, identification et
 * resynchronisation du cache. Pas de capture (aucune API sans geste utilisateur), pas de redémarrage.
 */
export function createWebAdapter(): PlayerAdapter {
  return {
    id: "player.web",
    capabilities: async () => ["cmd.reload", "cmd.identify", "cmd.cache.resync"],
    restart: () => Promise.reject(new Error("unsupported")),
    screenshot: () => Promise.reject(new Error("unsupported")),
  };
}
