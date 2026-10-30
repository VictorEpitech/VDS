import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { components } from "@vds/sdk-ts";
import { ApiError, useApi, type Api } from "../../lib/api";

type S = components["schemas"];
export type DiscoveryCapabilities = S["vds_server__discovery__schemas__CapabilitiesOut"];
export type Scan = S["ScanOut"];
export type DiscoveredDevice = S["DeviceOut"];

const keys = {
  capabilities: (siteId: string) => ["discovery", "capabilities", siteId] as const,
  scan: (siteId: string, scanId: string) => ["discovery", "scan", siteId, scanId] as const,
};

export const isRunning = (s: Scan["status"]) => s === "pending" || s === "running";

export function useDiscoveryCapabilities(siteId: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.capabilities(siteId),
    enabled: siteId !== "",
    retry: false,
    queryFn: () => api.get<DiscoveryCapabilities>(`/sites/${siteId}/discovery/capabilities`),
  });
}

/** Recherche à la demande : un POST au clic, puis interrogation toutes les secondes tant que le scan court (pas de WS admin en V1). */
export function useDiscoveryScan(siteId: string) {
  const api = useApi();
  const qc = useQueryClient();
  const [scanId, setScanId] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => api.post<S["ScanCreated"]>(`/sites/${siteId}/discovery/scans`, {}),
    onSuccess: (created) => {
      setScanId(created.scan_id);
      void qc.invalidateQueries({ queryKey: keys.scan(siteId, created.scan_id) });
    },
  });
  const scan = useQuery({
    queryKey: keys.scan(siteId, scanId ?? ""),
    enabled: scanId !== null,
    queryFn: () => api.get<Scan>(`/sites/${siteId}/discovery/scans/${scanId}`),
    refetchInterval: (q) => (q.state.data && !isRunning(q.state.data.status) ? false : 1000),
  });
  return { start, scan, started: scanId !== null, reset: () => setScanId(null) };
}

/** Nom d'écran proposé : nom annoncé, sinon « marque modèle », sinon adresse. */
export function suggestedName(d: DiscoveredDevice): string {
  return (d.name || [d.vendor, d.model].filter(Boolean).join(" ") || d.ip).slice(0, 120);
}

/**
 * Écrit au niveau écran la cascade (pilotes suggérés) puis, pour chaque pilote, les propriétés que son schéma annote
 * `x-vds-device-address` (ip / mac). Aucune clé de pilote n'est connue ici : tout vient du schéma.
 */
export async function applyDiscoveredDevice(api: Api, screenId: string, d: DiscoveredDevice): Promise<void> {
  if (d.suggested_drivers.length === 0) return;
  await api.put(`/config/screen/${screenId}/core/power.cascade`, { value: d.suggested_drivers });
  for (const driver of d.suggested_drivers) {
    let schema: { properties?: Record<string, Record<string, unknown>> };
    try {
      schema = (await api.get<{ config_schema: typeof schema }>(`/registry/modules/${driver}/settings`)).config_schema;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) continue;
      throw e;
    }
    for (const [key, field] of Object.entries(schema.properties ?? {})) {
      const kind = field["x-vds-device-address"];
      const scopes = field["x-vds-scopes"];
      if (Array.isArray(scopes) && !scopes.includes("screen")) continue;
      const value = kind === "ip" ? d.ip : kind === "mac" ? d.mac : null;
      if (value) await api.put(`/config/screen/${screenId}/${driver}/${key}`, { value });
    }
  }
}
