/**
 * Canal WebSocket facultatif `<base>/ws/player` (docs/architecture-lot3.md § 1.3).
 *
 * Ne sert qu'à la réactivité : le serveur pousse `schedule.changed`, le player refait alors sa
 * synchronisation HTTP (ETag). Le heartbeat et le sondage HTTP restent la voie nominale : si le canal
 * est coupé, refusé ou bloqué par un proxy, rien ne change pour la lecture ni le hors ligne.
 * L'URL dérive de l'origine courante de la page (jamais de `wsBase` : absolu avec `VDS_PUBLIC_ORIGIN`,
 * invisible du relais Android 127.0.0.1) et du préfixe `VDS_BASE_PATH` déjà contenu dans `server`.
 */
export const SUBPROTOCOL = "vds.player.v1";
export const CLOSE_REVOKED = 4410;
export const CLOSE_VERSION_UNSUPPORTED = 4426;
export const BACKOFF_MIN_MS = 5_000;
export const BACKOFF_MAX_MS = 300_000;

/** `http(s)://hôte/<base>/` -> `ws(s)://hôte/<base>/ws/player`. */
export function wsUrl(server: string): string {
  const u = new URL("ws/player", server);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.href;
}

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  send(data: string): void;
  close(code?: number): void;
}

export type SocketFactory = (url: string, protocols: string[]) => SocketLike;

export interface PlayerSocketDeps {
  server: string;
  token: () => string;
  /** Le serveur signale une révision différente : refaire la synchronisation HTTP. */
  onScheduleChanged: (revision: string) => void;
  /** 4410 : jeton révoqué, même traitement qu'un 401 HTTP. */
  onRevoked: () => void;
  /** Trame `cmd` du serveur (docs/architecture-commandes.md §3.2). */
  onCommand?: (cmd: { cmd_id: string; name: string; args?: Record<string, unknown>; expires_at: string }) => void;
  factory?: SocketFactory;
  random?: () => number;
}

export class PlayerSocket {
  private ws: SocketLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private delay = BACKOFF_MIN_MS;
  private stopped = true;
  private ready = false;

  constructor(private readonly deps: PlayerSocketDeps) {}

  get connected(): boolean {
    return this.ready;
  }

  /** Envoie une trame si le canal est prêt ; faux sinon (le heartbeat prend le relais). */
  sendFrame(type: string, data: unknown): boolean {
    if (!this.ready || !this.ws) return false;
    try {
      this.ws.send(JSON.stringify({ v: 1, type, data }));
      return true;
    } catch {
      return false;
    }
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.ready = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.detach();
  }

  private detach(): void {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try {
      ws.close(1000);
    } catch {
      /* déjà fermé */
    }
  }

  private connect(): void {
    if (this.stopped) return;
    let ws: SocketLike;
    try {
      const make = this.deps.factory ?? ((u, p) => new WebSocket(u, p) as unknown as SocketLike);
      ws = make(wsUrl(this.deps.server), [SUBPROTOCOL]);
    } catch {
      this.retry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      // `id` et `ts` ont des valeurs par défaut côté serveur (crypto.randomUUID absent hors contexte sécurisé).
      const frame = { v: 1, type: "auth", data: { token: this.deps.token() } };
      ws.send(JSON.stringify(frame));
    };
    ws.onmessage = (ev) => this.onFrame(ev.data);
    ws.onerror = () => {};
    ws.onclose = (ev) => {
      this.ws = null;
      this.ready = false;
      if (ev.code === CLOSE_REVOKED) {
        this.stopped = true;
        this.deps.onRevoked();
      } else if (ev.code === CLOSE_VERSION_UNSUPPORTED) {
        this.stopped = true; // serveur plus ancien : sondage HTTP seul
      } else {
        this.retry();
      }
    };
  }

  private onFrame(raw: unknown): void {
    if (typeof raw !== "string") return;
    let f: { type?: unknown; data?: { revision?: unknown; cmd_id?: unknown; name?: unknown; expires_at?: unknown; args?: unknown } };
    try {
      f = JSON.parse(raw) as typeof f;
    } catch {
      return;
    }
    if (f.type === "welcome") {
      this.ready = true;
      this.delay = BACKOFF_MIN_MS;
      if (typeof f.data?.revision === "string") this.deps.onScheduleChanged(f.data.revision);
    } else if (f.type === "cmd") {
      const d = f.data;
      if (d && typeof d.cmd_id === "string" && typeof d.name === "string" && typeof d.expires_at === "string") {
        const args = d.args && typeof d.args === "object" ? (d.args as Record<string, unknown>) : {};
        this.deps.onCommand?.({ cmd_id: d.cmd_id, name: d.name, args, expires_at: d.expires_at });
      }
    } else if (f.type === "schedule.changed" && typeof f.data?.revision === "string") {
      this.deps.onScheduleChanged(f.data.revision);
    }
  }

  private retry(): void {
    if (this.stopped || this.timer) return;
    const jitter = 0.5 + (this.deps.random ?? Math.random)();
    const wait = Math.min(this.delay * jitter, BACKOFF_MAX_MS);
    this.delay = Math.min(this.delay * 2, BACKOFF_MAX_MS);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, wait);
  }
}
