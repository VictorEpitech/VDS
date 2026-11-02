import { ApiError, type Api } from "../../lib/api";
import type { Command } from "../../lib/queries";
import { isFinal, newRequestId, POLL_MS } from "../supervision/commands";

export const DELAY_MS = 8_000;
export const MAX_PARALLEL = 4;
export const MAX_SELECTION = 200;
/** Même bornes que la commande unitaire (120 relevés de 700 ms). */
const COMMAND_TIMEOUT_MS = 120 * POLL_MS;
/** Mise à jour de l'application : délai de commande de l'agent 300 s, plus une marge. */
const APK_TIMEOUT_MS = 330_000;

export type BatchKind = "on" | "off" | "group" | "delete" | "apk_update";
export type Phase = "waiting" | "running" | "done";

export interface Target {
  screenId: string;
  name: string;
  siteId: string;
  /** Agent en ligne du site (allumer / éteindre). */
  agentId?: string;
  /** Réutilisé tel quel pour rejouer une requête restée sans réponse HTTP. */
  requestId?: string;
}

export type Outcome = { status: "pending" } | { status: "ok" } | { status: "failed"; command?: Command; error?: unknown; requestId?: string };

export interface Batch {
  id: number;
  kind: BatchKind;
  groupId: string | null;
  targets: Target[];
  /** Écrans écartés avant l'envoi (agent hors ligne, autre site…). */
  skipped: number;
  phase: Phase;
  dueAt: number;
  results: Record<string, Outcome>;
}

export interface EnqueueOptions {
  groupId?: string | null;
  skipped?: number;
  /** 0 = envoi immédiat (suppression et mise à jour confirmées, nouvel essai). */
  delayMs?: number;
}

interface Waiter {
  resolve: (c: Command | null) => void;
  deadline: number;
}

/**
 * File d'actions applicative : envoi différé, 4 requêtes en parallèle, un sondage par agent.
 * Hors React pour survivre à la navigation ; le fournisseur l'expose via `useSyncExternalStore`.
 */
export class ActionQueue {
  private batches: Batch[] = [];
  private listeners = new Set<() => void>();
  private timers = new Map<number, ReturnType<typeof setTimeout>>();
  private pollers = new Map<string, { siteId: string; agentId: string; waiters: Map<string, Waiter>; timer: ReturnType<typeof setTimeout> | null }>();
  private active = 0;
  private waitingSlots: (() => void)[] = [];
  private seq = 0;

  constructor(
    private readonly api: Api,
    private readonly onSettled: () => void = () => {},
  ) {}

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = () => this.batches;

  /** Vrai tant qu'une action n'est pas terminée (avertissement à la fermeture de l'onglet). */
  hasUnfinished(): boolean {
    return this.batches.some((b) => b.phase !== "done");
  }

  private set(next: Batch[]) {
    this.batches = next;
    this.listeners.forEach((l) => l());
  }

  private patch(id: number, fn: (b: Batch) => Batch) {
    this.set(this.batches.map((b) => (b.id === id ? fn(b) : b)));
  }

  enqueue(kind: BatchKind, targets: Target[], opts: EnqueueOptions = {}): number {
    const delay = opts.delayMs ?? (kind === "delete" || kind === "apk_update" ? 0 : DELAY_MS);
    const id = ++this.seq;
    const batch: Batch = {
      id,
      kind,
      groupId: opts.groupId ?? null,
      targets,
      skipped: opts.skipped ?? 0,
      phase: delay > 0 ? "waiting" : "running",
      dueAt: Date.now() + delay,
      results: Object.fromEntries(targets.map((t) => [t.screenId, { status: "pending" } as Outcome])),
    };
    this.set([...this.batches, batch]);
    if (delay > 0) this.timers.set(id, setTimeout(() => this.start(id), delay));
    else void this.run(batch);
    return id;
  }

  private start(id: number) {
    this.timers.delete(id);
    const b = this.batches.find((x) => x.id === id);
    if (!b || b.phase !== "waiting") return;
    this.patch(id, (x) => ({ ...x, phase: "running" }));
    void this.run({ ...b, phase: "running" });
  }

  /** Annule une action encore en attente : rien n'a été envoyé. */
  cancel(id: number): boolean {
    const b = this.batches.find((x) => x.id === id);
    if (!b || b.phase !== "waiting") return false;
    clearTimeout(this.timers.get(id));
    this.timers.delete(id);
    this.set(this.batches.filter((x) => x.id !== id));
    return true;
  }

  dismiss(id: number) {
    const b = this.batches.find((x) => x.id === id);
    if (b?.phase === "done") this.set(this.batches.filter((x) => x.id !== id));
  }

  /** Relance uniquement les échecs, dans une nouvelle action (nouveaux `request_id`). */
  retryFailed(id: number): number | null {
    const b = this.batches.find((x) => x.id === id);
    if (!b || b.phase !== "done") return null;
    const failed = b.targets
      .filter((t) => b.results[t.screenId]?.status === "failed")
      .map((t) => {
        const o = b.results[t.screenId];
        const requestId = o?.status === "failed" ? o.requestId : undefined;
        return requestId ? { ...t, requestId } : { ...t, requestId: undefined };
      });
    if (!failed.length) return null;
    this.dismiss(id);
    return this.enqueue(b.kind, failed, { groupId: b.groupId, delayMs: 0 });
  }

  private async run(batch: Batch) {
    await Promise.all(
      batch.targets.map(async (t) => {
        const requestId = t.requestId ?? newRequestId();
        const settled = await this.slot(() => this.send(batch, t, requestId)).then(
          (sent) => ("status" in sent ? sent : this.settle(batch, t, sent.command)),
          (error: unknown): Promise<Outcome> => Promise.resolve({ status: "failed", error, ...(error instanceof ApiError ? {} : { requestId }) }),
        );
        const outcome = await settled;
        this.patch(batch.id, (b) => ({ ...b, results: { ...b.results, [t.screenId]: outcome } }));
      }),
    );
    this.patch(batch.id, (b) => ({ ...b, phase: "done" }));
    this.onSettled();
  }

  /** Envoie la requête d'un écran ; une commande d'alimentation attend ensuite le sondage de l'agent. */
  private async send(batch: Batch, t: Target, requestId: string): Promise<Outcome | { command: Command }> {
    if (batch.kind === "delete") {
      await this.api.delete(`/screens/${t.screenId}`);
      return { status: "ok" };
    }
    if (batch.kind === "group") {
      await this.api.patch(`/screens/${t.screenId}`, { group_id: batch.groupId });
      return { status: "ok" };
    }
    if (!t.agentId) return { status: "failed" };
    if (batch.kind === "apk_update") {
      const command = await this.api.post<Command>(`/sites/${t.siteId}/agents/${t.agentId}/commands`, { name: "apk.update", screen_id: t.screenId, args: { request_id: requestId } });
      return { command };
    }
    const command = await this.api.post<Command>(`/sites/${t.siteId}/agents/${t.agentId}/commands`, {
      name: "power.set",
      screen_id: t.screenId,
      args: { state: batch.kind, reason: "manual", request_id: requestId },
      ttl_s: 120,
    });
    return { command };
  }

  private async settle(batch: Batch, t: Target, sent: Command): Promise<Outcome> {
    const timeout = batch.kind === "apk_update" ? APK_TIMEOUT_MS : COMMAND_TIMEOUT_MS;
    const done = isFinal(sent) || !t.agentId ? sent : await this.poll(t.siteId, t.agentId, sent, timeout);
    return done.status === "ok" ? { status: "ok" } : { status: "failed", command: done };
  }

  private async slot<T>(fn: () => Promise<T>): Promise<T> {
    if (this.active >= MAX_PARALLEL) await new Promise<void>((r) => this.waitingSlots.push(r));
    this.active++;
    try {
      return await fn();
    } finally {
      this.active--;
      this.waitingSlots.shift()?.();
    }
  }

  /** Un seul `GET .../commands` par agent et par tour pour toutes ses commandes en attente. */
  private poll(siteId: string, agentId: string, sent: Command, timeoutMs: number): Promise<Command> {
    const key = `${siteId}/${agentId}`;
    let p = this.pollers.get(key);
    if (!p) {
      p = { siteId, agentId, waiters: new Map(), timer: null };
      this.pollers.set(key, p);
    }
    const poller = p;
    return new Promise<Command>((resolve) => {
      poller.waiters.set(sent.id, { resolve: (c) => resolve(c ?? { ...sent, status: "expired" }), deadline: Date.now() + timeoutMs });
      if (!poller.timer) poller.timer = setTimeout(() => void this.tick(key), POLL_MS);
    });
  }

  private async tick(key: string) {
    const p = this.pollers.get(key);
    if (!p) return;
    p.timer = null;
    try {
      const list = await this.api.get<Command[]>(`/sites/${p.siteId}/agents/${p.agentId}/commands`);
      for (const [id, w] of [...p.waiters]) {
        const c = list.find((x) => x.id === id);
        if (c && isFinal(c)) {
          p.waiters.delete(id);
          w.resolve(c);
        }
      }
    } catch {
      /* échec ponctuel du relevé : on réessaie jusqu'à l'échéance */
    }
    const now = Date.now();
    for (const [id, w] of [...p.waiters]) {
      if (w.deadline <= now) {
        p.waiters.delete(id);
        w.resolve(null);
      }
    }
    if (p.waiters.size) p.timer = setTimeout(() => void this.tick(key), POLL_MS);
    else this.pollers.delete(key);
  }

  /** Arrêt propre (démontage du fournisseur) : plus aucun envoi différé. */
  dispose() {
    this.timers.forEach((t) => clearTimeout(t));
    this.timers.clear();
    const pollers = [...this.pollers.values()];
    this.pollers.clear();
    for (const p of pollers) {
      if (p.timer) clearTimeout(p.timer);
      p.timer = null;
      const waiters = [...p.waiters.values()];
      p.waiters.clear();
      waiters.forEach((w) => w.resolve(null));
    }
  }
}
