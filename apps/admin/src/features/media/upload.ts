import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import { keys, type Media } from "../../lib/queries";

export interface UploadJob {
  key: number;
  name: string;
  /** 0..1 */
  progress: number;
  state: "uploading" | "done" | "error";
  error?: ApiError;
}

/** File d'envois : un envoi à la fois par fichier, progression suivie, médiathèque rafraîchie à la fin. */
export function useUploads() {
  const api = useApi();
  const qc = useQueryClient();
  const [jobs, setJobs] = useState<UploadJob[]>([]);

  const patch = useCallback((key: number, change: Partial<UploadJob>) => {
    setJobs((list) => list.map((j) => (j.key === key ? { ...j, ...change } : j)));
  }, []);

  const start = useCallback(
    (files: File[]) => {
      files.forEach((file) => {
        const key = Date.now() * 1000 + Math.floor(Math.random() * 1000);
        setJobs((list) => [...list, { key, name: file.name, progress: 0, state: "uploading" }]);
        api
          .upload<Media>("/media", file, {}, (progress) => patch(key, { progress }))
          .then(() => {
            patch(key, { state: "done", progress: 1 });
            void qc.invalidateQueries({ queryKey: keys.media });
          })
          .catch((err: unknown) => {
            patch(key, { state: "error", error: err instanceof ApiError ? err : new ApiError(0, "errors.unknown") });
          });
      });
    },
    [api, patch, qc],
  );

  const dismiss = useCallback((key: number) => setJobs((list) => list.filter((j) => j.key !== key)), []);
  return { jobs, start, dismiss };
}
