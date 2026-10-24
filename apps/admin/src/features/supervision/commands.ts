import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import { agentKeys, type Command } from "../../lib/queries";

const FINAL = new Set(["ok", "failed", "expired", "rejected"]);
export const POLL_MS = 700;
const MAX_POLLS = 120;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export const isFinal = (c: Command) => FINAL.has(c.status);

interface Request {
  siteId: string;
  agentId: string;
  name: "power.set" | "power.probe" | "power.detect";
  screenId: string;
  args?: Record<string, unknown>;
}

/** Envoie une commande à l'agent du site puis attend son résultat (l'agent répond de manière asynchrone). */
export function useRemoteCommand() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (r: Request): Promise<Command> => {
      const base = `/sites/${r.siteId}/agents/${r.agentId}/commands`;
      const sent = await api.post<Command>(base, { name: r.name, screen_id: r.screenId, args: r.args ?? {}, ttl_s: 120 });
      let current = sent;
      for (let i = 0; i < MAX_POLLS && !isFinal(current); i++) {
        await sleep(POLL_MS);
        const list = await api.get<Command[]>(base);
        current = list.find((c) => c.id === sent.id) ?? current;
      }
      void qc.invalidateQueries({ queryKey: agentKeys.commands(r.siteId, r.agentId) });
      return current;
    },
  });
}

/** `randomUUID` n'existe qu'en contexte sécurisé (HTTPS/localhost) ; `getRandomValues` reste disponible en HTTP sur le LAN. */
export function newRequestId(): string {
  const c = globalThis.crypto;
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = ((b[6] ?? 0) & 0x0f) | 0x40;
  b[8] = ((b[8] ?? 0) & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function powerSetArgs(state: "on" | "off"): Record<string, unknown> {
  return { state, reason: "manual", request_id: newRequestId() };
}
