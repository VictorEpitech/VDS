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
  put<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body?: unknown): Promise<T>;
  delete<T = void>(path: string): Promise<T>;
  /** Envoi multipart avec progression (0..1) ; `fields` s'ajoute au fichier (champ `file`). */
  upload<T>(path: string, file: File, fields?: Record<string, string>, onProgress?: (ratio: number) => void): Promise<T>;
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
  if (!res.ok) throw parseError(res.status, await res.text().catch(() => ""), res.headers.get("Retry-After"));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function parseError(status: number, raw: string, retryHeader: string | null): ApiError {
  let parsed: Partial<ApiErrorBody> = {};
  try {
    parsed = JSON.parse(raw) as Partial<ApiErrorBody>;
  } catch {
    /* corps absent ou non JSON */
  }
  const retry = Number(retryHeader);
  return new ApiError(status, parsed.code ?? "errors.unknown", parsed.params ?? {}, retry > 0 ? retry : null);
}

function sendUpload<T>(
  url: string,
  file: File,
  fields: Record<string, string>,
  onProgress?: (ratio: number) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress?.(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "errors.server_unreachable"));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1);
        resolve(JSON.parse(xhr.responseText) as T);
      } else reject(parseError(xhr.status, xhr.responseText, xhr.getResponseHeader("Retry-After")));
    };
    const form = new FormData();
    form.append("file", file);
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    xhr.send(form);
  });
}

/** Client de l'API sous `apiBase` (déjà préfixé par VDS_BASE_PATH dans la config d'exécution). */
export function createApi(apiBase: string): Api {
  return {
    get: (path) => send(`${apiBase}${path}`, "GET"),
    post: (path, body) => send(`${apiBase}${path}`, "POST", body),
    put: (path, body) => send(`${apiBase}${path}`, "PUT", body),
    patch: (path, body) => send(`${apiBase}${path}`, "PATCH", body),
    delete: (path) => send(`${apiBase}${path}`, "DELETE"),
    upload: (path, file, fields = {}, onProgress) => sendUpload(`${apiBase}${path}`, file, fields, onProgress),
  };
}

export function useApi(): Api {
  const { apiBase } = useRuntimeConfig();
  return useMemo(() => createApi(apiBase), [apiBase]);
}
