import { useEffect, useRef, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "../../lib/api";
import { useAgents } from "../../lib/queries";

type S = components["schemas"];
export type Capabilities = S["vds_server__provisioning__schemas__CapabilitiesOut"];
export type Provision = S["ProvisionOut"];
export type ProvisionCreate = S["ProvisionCreate"];
export type StepId = S["vds_server__provisioning__schemas__StepOut"]["step"];
export type StepStatus = S["vds_server__provisioning__schemas__StepOut"]["status"];

/** Ordre des étapes de l'assistant (contrat vds-core 1.3.0). */
export const STEP_ORDER: readonly StepId[] = ["connect", "authorize", "inspect", "download", "verify", "install", "device_owner", "soft_kiosk", "configure", "inject", "launch"];

const ACTIVE: ReadonlySet<Provision["status"]> = new Set(["queued", "running", "waiting_user", "waiting_online"]);
export const isActive = (status: Provision["status"]) => ACTIVE.has(status);

export const provisionKeys = {
  capabilities: (siteId: string) => ["provisioning", "capabilities", siteId] as const,
  session: (pid: string) => ["provisioning", pid] as const,
};

export function useCapabilities(siteId: string) {
  const api = useApi();
  return useQuery({
    queryKey: provisionKeys.capabilities(siteId),
    enabled: siteId !== "",
    queryFn: () => api.get<Capabilities>(`/provisioning/capabilities?site_id=${encodeURIComponent(siteId)}`),
  });
}

/** Vrai si au moins un site a un agent capable de `provision.android` (même source que l'assistant). */
export function useProvisioningAvailable(siteIds: readonly string[]) {
  const api = useApi();
  const results = useQueries({
    queries: siteIds.map((siteId) => ({
      queryKey: provisionKeys.capabilities(siteId),
      queryFn: () => api.get<Capabilities>(`/provisioning/capabilities?site_id=${encodeURIComponent(siteId)}`),
    })),
  });
  return results.some((r) => r.data?.available === true);
}

/** Suivi live : interrogation toutes les 2 s tant que la session n'est pas terminée (pas de WS admin en V1). */
export function useProvision(pid: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: provisionKeys.session(pid ?? ""),
    enabled: pid !== null,
    queryFn: () => api.get<Provision>(`/provisioning/${pid}`),
    refetchInterval: (q) => (q.state.data && !isActive(q.state.data.status) ? false : 2000),
  });
}

export function useProvisionActions(pid: string | null, onCreated: (p: Provision) => void) {
  const api = useApi();
  const qc = useQueryClient();
  const store = (p: Provision) => qc.setQueryData(provisionKeys.session(p.id), p);
  const create = useMutation({
    mutationFn: (v: { screenId: string; body: ProvisionCreate }) => api.post<Provision>(`/screens/${v.screenId}/provision`, v.body),
    onSuccess: (p) => {
      store(p);
      onCreated(p);
    },
  });
  const retry = useMutation({
    mutationFn: async (v: { fromStep: StepId | null }) => {
      return api.post<Provision>(`/provisioning/${pid}/retry`, { from_step: v.fromStep });
    },
    onSuccess: store,
  });
  const cancel = useMutation({
    mutationFn: () => api.post<Provision>(`/provisioning/${pid}/cancel`),
    onSuccess: store,
  });
  return { create, retry, cancel };
}

/** `provision.android.tls_required` -> `tls_required` ; `provision.ticket_expired` -> `ticket_expired`. */
export const codeKey = (code: string) => code.replace(/^provision\.(android\.)?/, "");

/** Délai sans progression d'une commande en file avant d'alerter. */
export const STALL_MS = 20_000;

/** Signature de progression : change dès que le statut ou une étape bouge. */
const progress = (p: Provision) => `${p.status}|${p.steps.map((s) => `${s.step}:${s.status}`).join(",")}`;

/**
 * Détecte une commande d'agent sans résultat : session `queued` sans progression au-delà de `STALL_MS`,
 * ou session en cours alors qu'aucun agent du site n'est connecté (API agents existante).
 */
export function useAgentStall(p: Provision | undefined, nowMs: () => number = Date.now) {
  const agents = useAgents(p?.site_id ?? "");
  const [, tick] = useState(0);
  const mark = useRef<{ sig: string; at: number } | null>(null);
  const sig = p ? progress(p) : null;
  if (sig !== null && mark.current?.sig !== sig) mark.current = { sig, at: nowMs() };
  const active = !!p && (p.status === "queued" || p.status === "running");
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  if (!p || !active) return { stalled: false, notStarted: false };
  const notStarted = p.status === "queued";
  const agentDown = agents.isSuccess && !agents.data.some((a) => a.status === "online");
  const idle = mark.current ? nowMs() - mark.current.at : 0;
  return { stalled: agentDown || (notStarted && idle >= STALL_MS), notStarted };
}
