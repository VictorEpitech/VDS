import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "../../lib/api";
import type { IconName } from "../../components/Icon";
import { keys, type Screen } from "../../lib/queries";
import { newRequestId } from "../supervision/commands";

type S = components["schemas"];
export type PlayerCommand = S["PlayerCommandOut"];
export type PlayerCommandName = PlayerCommand["name"];
export type PlayerCommandStatus = PlayerCommand["status"];
export type Screenshot = S["ScreenshotOut"];

/** Action proposée dans l'interface : une commande, ses arguments et la capacité requise côté appareil. */
export interface CommandAction {
  id: "reload" | "identify" | "screenshot" | "restart.app" | "restart.device" | "cache.resync";
  name: PlayerCommandName;
  args: Record<string, unknown>;
  capability: string;
  /** Administrateur seulement (cohérent avec Allumer / Éteindre). */
  adminOnly: boolean;
  /** Demande une confirmation explicite. */
  destructive: boolean;
  /** Rangée dans le volet Avancé. */
  advanced: boolean;
  icon: IconName;
}

export const ACTIONS: readonly CommandAction[] = [
  { id: "reload", name: "reload", args: {}, capability: "cmd.reload", adminOnly: false, destructive: false, advanced: false, icon: "rotate" },
  { id: "identify", name: "identify", args: {}, capability: "cmd.identify", adminOnly: false, destructive: false, advanced: false, icon: "detect" },
  { id: "screenshot", name: "screenshot", args: {}, capability: "cmd.screenshot", adminOnly: false, destructive: false, advanced: false, icon: "media" },
  { id: "restart.app", name: "restart", args: { scope: "app" }, capability: "cmd.restart.app", adminOnly: true, destructive: true, advanced: false, icon: "power" },
  { id: "restart.device", name: "restart", args: { scope: "device" }, capability: "cmd.restart.device", adminOnly: true, destructive: true, advanced: false, icon: "power" },
  { id: "cache.resync", name: "cache.resync", args: {}, capability: "cmd.cache.resync", adminOnly: true, destructive: false, advanced: true, icon: "copy" },
];

export const actionById = (id: string): CommandAction | undefined => ACTIONS.find((a) => a.id === id);

/**
 * Un vieux player (sans `capabilities` annoncées) ne propose que `reload` ; un écran sans appareil
 * ne propose rien. La liste vient toujours du serveur, jamais d'une déduction par plateforme.
 */
export function capabilitiesOf(screen: Pick<Screen, "capabilities" | "device">): readonly string[] {
  if (!screen.device || screen.device.presence === "revoked") return [];
  return screen.capabilities ?? [];
}

export const canScreenshot = (screen: Pick<Screen, "capabilities" | "device">) => capabilitiesOf(screen).includes("cmd.screenshot");

/** « Relancer l'application » : seul le serveur sait si un chemin (player ou agent) existe, il n'est jamais déduit ici. */
export const canRestartApp = (screen: Pick<Screen, "device"> & { restart_app?: Screen["restart_app"] }) =>
  !!screen.device && screen.device.presence !== "revoked" && screen.restart_app?.available === true;

export function availableActions(screen: Pick<Screen, "capabilities" | "device"> & { restart_app?: Screen["restart_app"] }, isAdmin: boolean): CommandAction[] {
  const caps = capabilitiesOf(screen);
  return ACTIONS.filter((a) => (isAdmin || !a.adminOnly) && (a.id === "restart.app" ? canRestartApp(screen) : caps.includes(a.capability)));
}

const TERMINAL: ReadonlySet<PlayerCommandStatus> = new Set(["ok", "failed", "expired", "rejected", "cancelled"]);
export const isTerminal = (c: Pick<PlayerCommand, "status">) => TERMINAL.has(c.status);
/** Annulable tant que le player ne l'a pas reçue. */
export const isCancellable = (c: Pick<PlayerCommand, "status">) => c.status === "queued";

export const STATUS_VIEW: Record<PlayerCommandStatus, { state: string; icon: IconName }> = {
  queued: { state: "pairing", icon: "expiry" },
  sent: { state: "overridden", icon: "processing" },
  ok: { state: "online", icon: "check" },
  failed: { state: "power_failed", icon: "failed" },
  rejected: { state: "power_failed", icon: "revoke" },
  expired: { state: "offline", icon: "offline" },
  cancelled: { state: "scheduled_off", icon: "back" },
};

/** Sondage : 1 s pendant 20 s, puis 5 s (jusqu'à l'état terminal ou l'expiration). */
export function pollDelay(ageMs: number): number {
  return ageMs < 20_000 ? 1_000 : 5_000;
}

export const commandKeys = {
  list: (screenId: string) => ["screen-commands", screenId] as const,
  shots: (screenId: string) => ["screen-shots", screenId] as const,
};

/** Commandes récentes d'un écran ; sondées tant qu'une n'est pas terminée. */
export function useScreenCommands(screenId: string, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: commandKeys.list(screenId),
    enabled,
    queryFn: () => api.get<PlayerCommand[]>(`/screens/${screenId}/commands?limit=10`),
    refetchInterval: (q) => {
      const open = (q.state.data ?? []).filter((c) => !isTerminal(c));
      if (!open.length) return false;
      const oldest = Math.min(...open.map((c) => new Date(c.created_at).getTime()));
      return pollDelay(Date.now() - oldest);
    },
  });
}

export function useScreenshots(screenId: string, enabled: boolean) {
  const api = useApi();
  return useQuery({ queryKey: commandKeys.shots(screenId), enabled, queryFn: () => api.get<Screenshot[]>(`/screens/${screenId}/screenshots?limit=10`) });
}

/** Envoie une commande ; la clé d'idempotence est tirée par clic (un double clic renvoie la même commande). */
export function useSendCommand(screenId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: CommandAction) => api.post<PlayerCommand>(`/screens/${screenId}/commands`, { name: a.name, args: a.args, request_key: newRequestId() }),
    onSettled: () => void qc.invalidateQueries({ queryKey: commandKeys.list(screenId) }),
  });
}

export function useCancelCommand(screenId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (commandId: string) => api.post<PlayerCommand>(`/screens/${screenId}/commands/${commandId}/cancel`),
    onSettled: () => void qc.invalidateQueries({ queryKey: commandKeys.list(screenId) }),
  });
}

/** Rafraîchit la carte quand une capture à la demande vient d'aboutir. */
export function refreshAfterTerminal(qc: ReturnType<typeof useQueryClient>, screenId: string) {
  void qc.invalidateQueries({ queryKey: keys.screens });
  void qc.invalidateQueries({ queryKey: commandKeys.shots(screenId) });
}

/** Âge lisible (« il y a 3 min ») dans la langue active. */
export function relativeAge(iso: string, locale: string, now = Date.now()): string {
  const s = Math.round((new Date(iso).getTime() - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(s, "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86_400), "day");
}
