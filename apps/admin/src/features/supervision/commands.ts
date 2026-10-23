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

export function powerSetArgs(state: "on" | "off"): Record<string, unknown> {
  return { state, reason: "manual", request_id: globalThis.crypto.randomUUID() };
}
