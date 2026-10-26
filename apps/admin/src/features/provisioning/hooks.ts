import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "../../lib/api";

type S = components["schemas"];
export type Capabilities = S["CapabilitiesOut"];
export type Provision = S["ProvisionOut"];
export type ProvisionCreate = S["ProvisionCreate"];
export type StepId = S["StepOut"]["step"];
export type StepStatus = S["StepOut"]["status"];

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
    mutationFn: (fromStep: StepId | null) => api.post<Provision>(`/provisioning/${pid}/retry`, { from_step: fromStep }),
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
