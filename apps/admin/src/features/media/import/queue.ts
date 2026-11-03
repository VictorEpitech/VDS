import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, useApi } from "../../../lib/api";
import { keys, type Media } from "../../../lib/queries";
import type { Owner } from "../../scope/scope";
import { inspectFile, type ImportWarning, type TargetOrientation } from "./inspect";

export interface ImportFileState {
  id: number;
  file: File;
  status: "pending" | "uploading" | "done" | "error";
  /** 0..1 */
  progress: number;
  /** Code d'erreur du serveur (`media.*`), traduit à l'affichage. */
  errorCode?: string;
  errorParams?: Record<string, unknown>;
  warnings: ImportWarning[];
  mediaId?: string;
  kind?: Media["kind"];
}

export const IMPORT_CONCURRENCY = 2;

/** File d'envoi de l'assistant : 2 envois en parallèle, avertissements lus en local, médiathèque rafraîchie à chaque envoi réussi. */
export function useImportQueue(opts: { target?: TargetOrientation; owner?: Owner; concurrency?: number } = {}) {
  const { target, owner, concurrency = IMPORT_CONCURRENCY } = opts;
  const api = useApi();
  const qc = useQueryClient();
  const [files, setFiles] = useState<ImportFileState[]>([]);
  const [running, setRunning] = useState(false);
  const launched = useRef(new Set<number>());
  const seq = useRef(0);

  const patch = useCallback((id: number, change: Partial<ImportFileState>) => {
    setFiles((list) => list.map((f) => (f.id === id ? { ...f, ...change } : f)));
  }, []);

  const add = useCallback(
    (list: File[]) => {
      const added = list.map((file) => ({ id: ++seq.current, file, status: "pending" as const, progress: 0, warnings: [] as ImportWarning[] }));
      if (added.length === 0) return;
      setFiles((cur) => [...cur, ...added]);
      for (const a of added) void inspectFile(a.file, target).then((warnings) => patch(a.id, { warnings }));
    },
    [patch, target],
  );

  const remove = useCallback((id: number) => setFiles((list) => list.filter((f) => f.id !== id)), []);

  const retry = useCallback(
    (id: number) => {
      launched.current.delete(id);
      patch(id, { status: "pending", progress: 0, errorCode: undefined, errorParams: undefined });
      setRunning(true);
    },
    [patch],
  );

  useEffect(() => {
    if (!running) return;
    const active = files.filter((f) => f.status === "uploading").length;
    const next = files.filter((f) => f.status === "pending" && !launched.current.has(f.id)).slice(0, Math.max(0, concurrency - active));
    // Propriétaire explicite : champs multipart du serveur ; absent ou partagé : défaut du serveur.
    const fields: Record<string, string> = owner && owner.type !== "organization" ? { owner_type: owner.type, owner_id: owner.id } : {};
    for (const f of next) {
      launched.current.add(f.id);
      patch(f.id, { status: "uploading", progress: 0 });
      api
        .upload<Media>("/media", f.file, fields, (progress) => patch(f.id, { progress }))
        .then((m) => {
          patch(f.id, { status: "done", progress: 1, mediaId: m.id, kind: m.kind });
          void qc.invalidateQueries({ queryKey: keys.media });
        })
        .catch((err: unknown) => {
          const e = err instanceof ApiError ? err : new ApiError(0, "errors.unknown");
          patch(f.id, { status: "error", errorCode: e.code, errorParams: e.params });
        });
    }
  }, [api, concurrency, files, owner, patch, qc, running]);

  const settled = files.length > 0 && files.every((f) => f.status === "done" || f.status === "error");
  return { files, add, remove, retry, start: () => setRunning(true), running, settled };
}
