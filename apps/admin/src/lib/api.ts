import type { ApiErrorBody } from "@vds/sdk-ts";
import { useMemo } from "react";
import { useRuntimeConfig } from "./runtime-config";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly params: Record<string, unknown> = {},
    readonly retryAfter: number | null = null,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

export interface Api {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "errors.server_unreachable");
  }
  if (!res.ok) {
    let parsed: Partial<ApiErrorBody> = {};
    try {
      parsed = (await res.json()) as Partial<ApiErrorBody>;
    } catch {
      /* corps absent ou non JSON */
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, parsed.code ?? "errors.unknown", parsed.params ?? {}, retry > 0 ? retry : null);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Client de l'API sous `apiBase` (déjà préfixé par VDS_BASE_PATH dans la config d'exécution). */
export function createApi(apiBase: string): Api {
  return {
    get: (path) => send(`${apiBase}${path}`, "GET"),
    post: (path, body) => send(`${apiBase}${path}`, "POST", body),
  };
}

export function useApi(): Api {
  const { apiBase } = useRuntimeConfig();
  return useMemo(() => createApi(apiBase), [apiBase]);
}
