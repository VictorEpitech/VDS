/**
 * Commandes player (vds-core 1.11.0, docs/architecture-commandes.md §3).
 *
 * Reçues par WS (`cmd`) ou par le heartbeat (`commands`), exécutées au plus une fois grâce au journal
 * `cmd_journal` (IndexedDB, repli sessionStorage qui survit à un rechargement). Le journal est écrit AVANT
 * toute action qui recharge ou redémarre : une commande redélivrée ne peut pas provoquer de boucle.
 * Règle 6 : aucun résultat, échec ou capture n'a de trace à l'écran ; seul `identify` affiche le nom de l'écran.
 */
import { MINIMAL_CAPABILITIES } from "@vds/sdk-ts";
import type { PlayerAdapter, PlayerCapability } from "@vds/sdk-ts";
import { kvGet, kvSet } from "./db";

/** Miroir de `wire.generated.ts` › CmdResult. */
export interface CmdResult {
  cmd_id: string;
  status: "ok" | "failed" | "expired" | "rejected";
  code: string | null;
  data: Record<string, unknown>;
}
export interface IncomingCommand {
  cmd_id: string;
  name: string;
  args?: Record<string, unknown>;
  expires_at: string;
}
export type Outcome = Pick<CmdResult, "status"> & { code?: string | null; data?: Record<string, unknown> };

export const JOURNAL_KEY = "cmd_journal";
export const JOURNAL_MAX = 200;
export const JOURNAL_TTL_MS = 48 * 3_600_000;
export const RESTART_OBSERVE_MS = 120_000;
export const MAX_PENDING_RESULTS = 20;
/** Plus de 3 rechargements/redémarrages en 10 min : refusés (filet contre toute boucle). */
export const RESTART_BURST = 3;
export const RESTART_BURST_WINDOW_MS = 600_000;

interface Entry {
  id: string;
  name: string;
  at: number;
  state: "executing" | "done";
  /** `boot_id` du shell qui a lancé un reload/restart. */
  boot?: string;
  result?: CmdResult;
  acked: boolean;
}

export interface Actions {
  identify(seconds: number): Promise<Outcome>;
  resync(): Promise<Outcome>;
  screenshot(cmdId: string): Promise<Outcome>;
  reload(): void;
}

export interface RunnerDeps {
  now: () => number;
  bootId: string;
  adapter: () => PlayerAdapter | null;
  actions: Actions;
  /** Poussée immédiate d'un résultat (WS), au mieux : le heartbeat le renvoie tant qu'il n'est pas acquitté. */
  send?: (r: CmdResult) => void;
}

const SESSION_KEY = "vds_cmd_journal";

function sessionRead(): Entry[] {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Entry[]) : [];
  } catch {
    return [];
  }
}

export function newBootId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    /* contexte non sécurisé */
  }
  const b = new Uint8Array(16);
  for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export class CommandRunner {
  private entries: Entry[] = [];
  private chain: Promise<void> = Promise.resolve();
  private caps: PlayerCapability[] = [...MINIMAL_CAPABILITIES];

  constructor(private readonly deps: RunnerDeps) {}

  /** Charge le journal, réévalue les capacités, conclut les reload/restart d'un démarrage précédent. */
  async init(): Promise<void> {
    const stored = ((await kvGet<Entry[]>(JOURNAL_KEY).catch(() => undefined)) ?? []) as Entry[];
    const byId = new Map<string, Entry>();
    for (const e of [...sessionRead(), ...stored]) {
      const cur = byId.get(e.id);
      if (!cur || (cur.state === "executing" && e.state === "done")) byId.set(e.id, e);
    }
    this.entries = [...byId.values()];
    try {
      const a = this.deps.adapter();
      this.caps = a ? await a.capabilities() : [...MINIMAL_CAPABILITIES];
    } catch {
      this.caps = [...MINIMAL_CAPABILITIES];
    }
    for (const e of this.entries) {
      if (e.state === "executing" && e.boot !== undefined && e.boot !== this.deps.bootId) {
        this.finish(e, { cmd_id: e.id, status: "ok", code: null, data: {} });
      }
    }
    this.prune();
    await this.save();
  }

  capabilities(): PlayerCapability[] {
    return [...this.caps];
  }

  /** Résultats non acquittés (≤ 20) pour le heartbeat. */
  pendingResults(): CmdResult[] {
    return this.entries
      .filter((e) => e.state === "done" && e.result && !e.acked)
      .slice(0, MAX_PENDING_RESULTS)
      .map((e) => e.result as CmdResult);
  }

  ack(ids: readonly string[]): void {
    const set = new Set(ids);
    let changed = false;
    for (const e of this.entries) {
      if (set.has(e.id) && !e.acked) {
        e.acked = true;
        changed = true;
      }
    }
    if (changed) void this.save();
  }

  /** Un reload/restart lancé mais non observé après 2 min : `failed`. Appelé à chaque heartbeat. */
  sweep(): void {
    const t = this.deps.now();
    let changed = false;
    for (const e of this.entries) {
      if (e.state === "executing" && e.boot === this.deps.bootId && t - e.at > RESTART_OBSERVE_MS) {
        this.finish(e, { cmd_id: e.id, status: "failed", code: "cmd.restart_not_observed", data: {} });
        changed = true;
      }
    }
    if (changed) void this.save();
  }

  handleAll(cmds: readonly IncomingCommand[]): void {
    for (const c of cmds) void this.handle(c);
  }

  handle(c: IncomingCommand): Promise<void> {
    const run = this.chain.then(() => this.exec(c)).catch(() => {});
    this.chain = run;
    return run;
  }

  private find(id: string): Entry | undefined {
    return this.entries.find((e) => e.id === id);
  }

  private finish(e: Entry, r: CmdResult): void {
    e.state = "done";
    e.result = r;
    e.acked = false;
    try {
      this.deps.send?.(r);
    } catch {
      /* canal indisponible : le heartbeat renvoie */
    }
  }

  private prune(): void {
    const limit = this.deps.now() - JOURNAL_TTL_MS;
    this.entries = this.entries.filter((e) => e.at >= limit || e.state === "executing");
    if (this.entries.length > JOURNAL_MAX) {
      this.entries.sort((a, b) => a.at - b.at);
      this.entries = this.entries.slice(-JOURNAL_MAX);
    }
  }

  private async save(): Promise<void> {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(this.entries));
    } catch {
      /* indisponible */
    }
    await kvSet(JOURNAL_KEY, this.entries).catch(() => {});
  }

  private record(c: IncomingCommand, o: Outcome): Entry {
    const e: Entry = { id: c.cmd_id, name: c.name, at: this.deps.now(), state: "executing", acked: false };
    this.entries.push(e);
    this.finish(e, { cmd_id: c.cmd_id, status: o.status, code: o.code ?? null, data: o.data ?? {} });
    return e;
  }

  private capFor(c: IncomingCommand): PlayerCapability | null {
    switch (c.name) {
      case "reload":
        return "cmd.reload";
      case "identify":
        return "cmd.identify";
      case "screenshot":
        return "cmd.screenshot";
      case "cache.resync":
        return "cmd.cache.resync";
      case "restart":
        return c.args?.scope === "app" ? "cmd.restart.app" : c.args?.scope === "device" ? "cmd.restart.device" : null;
      default:
        return null;
    }
  }

  private async exec(c: IncomingCommand): Promise<void> {
    if (!c || typeof c.cmd_id !== "string" || typeof c.name !== "string") return;
    const known = this.find(c.cmd_id);
    if (known) {
      // Déjà vue : jamais réexécutée. Son résultat est renvoyé s'il n'est pas acquitté.
      if (known.state === "done" && known.result && !known.acked) this.deps.send?.(known.result);
      return;
    }
    const exp = Date.parse(c.expires_at);
    if (!Number.isFinite(exp) || exp <= this.deps.now()) {
      this.record(c, { status: "expired" });
      return this.save();
    }
    const cap = this.capFor(c);
    if (c.name === "restart" && cap === null) {
      this.record(c, { status: "rejected", code: "cmd.invalid_args" });
      return this.save();
    }
    if (cap === null) {
      this.record(c, { status: "rejected", code: "unknown_command" });
      return this.save();
    }
    if (!this.caps.includes(cap)) {
      this.record(c, { status: "rejected", code: "unsupported" });
      return this.save();
    }
    if (c.name === "reload" || c.name === "restart") return this.execRestart(c);
    const entry: Entry = { id: c.cmd_id, name: c.name, at: this.deps.now(), state: "executing", acked: false };
    this.entries.push(entry);
    await this.save();
    let out: Outcome;
    try {
      if (c.name === "identify") {
        const s = Number(c.args?.seconds);
        const seconds = Number.isFinite(s) ? Math.min(30, Math.max(5, Math.round(s))) : 10;
        out = await this.deps.actions.identify(seconds);
      } else if (c.name === "screenshot") out = await this.deps.actions.screenshot(c.cmd_id);
      else out = await this.deps.actions.resync();
    } catch {
      out = { status: "failed", code: "cmd.failed" };
    }
    this.finish(entry, { cmd_id: c.cmd_id, status: out.status, code: out.code ?? null, data: out.data ?? {} });
    this.prune();
    await this.save();
  }

  private async execRestart(c: IncomingCommand): Promise<void> {
    const t = this.deps.now();
    const recent = this.entries.filter(
      (e) => (e.name === "reload" || e.name === "restart") && e.boot !== undefined && t - e.at < RESTART_BURST_WINDOW_MS,
    );
    if (recent.length >= RESTART_BURST) {
      this.record(c, { status: "rejected", code: "cmd.rate_limited" });
      return this.save();
    }
    const entry: Entry = { id: c.cmd_id, name: c.name, at: t, state: "executing", boot: this.deps.bootId, acked: false };
    this.entries.push(entry);
    await this.save(); // AVANT d'agir : le prochain démarrage verra la commande
    const scope = c.args?.scope;
    try {
      if (c.name === "reload") this.deps.actions.reload();
      else {
        const a = this.deps.adapter();
        if (!a) throw new Error("no_adapter");
        await a.restart(scope === "device" ? "device" : "app");
      }
    } catch {
      this.finish(entry, { cmd_id: c.cmd_id, status: "failed", code: "cmd.restart_failed", data: {} });
      await this.save();
    }
    // Sinon : `executing`, conclue au démarrage suivant (boot_id changé) ou par sweep() après 2 min.
  }
}
