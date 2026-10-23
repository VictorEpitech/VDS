import { ERR_DEVICE_REVOKED } from "./shared";

export class DeviceRevokedError extends Error {
  constructor() {
    super(ERR_DEVICE_REVOKED);
    this.name = "DeviceRevokedError";
  }
}

/** Réseau injoignable, délai dépassé ou réponse inexploitable : le player continue hors ligne. */
export class NetworkError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "NetworkError";
  }
}

/** URL du serveur (préfixe compris) : relative à la page `<base>/player/`, jamais en dur. */
export function serverUrl(): string {
  return new URL("..", location.href).href;
}

export function resolveServer(relative: string, base: string = serverUrl()): string {
  return new URL(relative, base).href;
}

const TIMEOUT_MS = 15_000;

export interface RequestOptions {
  method?: string;
  token?: string | null;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** Requête JSON vers le serveur. Lève `DeviceRevokedError` sur 401 `errors.device_revoked`. */
export async function request(path: string, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  opts.signal?.addEventListener("abort", () => ctl.abort(), { once: true });
  let res: Response;
  try {
    res = await fetch(resolveServer(path), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
      credentials: "omit",
      signal: ctl.signal,
    });
  } catch (e) {
    throw new NetworkError(e instanceof Error ? e.message : "fetch");
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401) {
    const code = await errorCode(res);
    if (code === ERR_DEVICE_REVOKED) throw new DeviceRevokedError();
  }
  return res;
}

export async function errorCode(res: Response): Promise<string | null> {
  try {
    const body = (await res.clone().json()) as { code?: unknown };
    return typeof body.code === "string" ? body.code : null;
  } catch {
    return null;
  }
}
