import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../auth/error-text";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { MediaStatus } from "../components/MediaStatus";
import { useUploads } from "../features/media/upload";
import { ApiError, useApi } from "../lib/api";
import { useContentTypes } from "../lib/content-types";
import { keys, useMedia, usePlaylists, type Media } from "../lib/queries";

const formatBytes = (n: number, lang: string) => {
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 }).format(v)} ${units[i]}`;
};

export function MediaLibrary() {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const api = useApi();
  const qc = useQueryClient();
  const media = useMedia();
  const contentTypes = useContentTypes();
  const playlists = usePlaylists();
  const { jobs, start, dismiss } = useUploads();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [blocked, setBlocked] = useState<{ media: Media; error: ApiError } | null>(null);
  const remove = useMutation({
    mutationFn: (m: Media) => api.delete(`/media/${m.id}`),
    onMutate: () => setBlocked(null),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.media }),
    onError: (error, m) => {
      if (error instanceof ApiError && error.code === "media.in_use") setBlocked({ media: m, error });
    },
  });

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    start([...e.dataTransfer.files]);
  };

  const usedBy = (err: ApiError): string[] => {
    const ids = Array.isArray(err.params.playlists) ? (err.params.playlists as string[]) : [];
    return ids.map((id) => playlists.data?.find((p) => p.id === id)?.name ?? "").filter(Boolean);
  };

  return (
    <>
      <h1>{t("media.title")}</h1>
      <p className="lead">{t("media.intro")}</p>

      <section className="card" aria-labelledby="upload-title">
        <h2 id="upload-title">{t("media.upload.title")}</h2>
        <div
          className="dropzone"
          data-dragging={dragging || undefined}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <Icon name="upload" />
          <p>{t("media.upload.drop")}</p>
          <button type="button" className="btn btn-filled" onClick={() => inputRef.current?.click()}>
            <Icon name="upload" />
            {t("media.upload.choose")}
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={contentTypes.acceptedMime.join(",")}
            className="visually-hidden"
            aria-label={t("media.upload.input")}
            tabIndex={-1}
            onChange={(e) => {
              start([...(e.target.files ?? [])]);
              e.target.value = "";
            }}
          />
        </div>
        <p className="field-help">{t("media.upload.accepts", { types: contentTypes.types.map((c) => c.name[i18n.language === "en" ? "en" : "fr"]).join(", ") })}</p>
        {jobs.length > 0 ? (
          <ul className="uploads list-reset" aria-label={t("media.upload.list")}>
            {jobs.map((j) => (
              <li key={j.key} className="upload" data-state={j.state}>
                <span className="upload-name">{j.name}</span>
                {j.state === "uploading" ? (
                  <>
                    <progress max={100} value={Math.round(j.progress * 100)} aria-label={t("media.upload.progress", { name: j.name })} />
                    <span className="upload-pct">{t("media.upload.percent", { percent: Math.round(j.progress * 100) })}</span>
                  </>
                ) : j.state === "done" ? (
                  <span className="status-badge" data-state="online">
                    <Icon name="online" />
                    <span>{t("media.upload.done")}</span>
                  </span>
                ) : (
                  <>
                    <span className="status-badge" data-state="offline" role="alert">
                      <Icon name="failed" />
                      <span>{errorText(j.error?.code ?? "errors.unknown", j.error?.params)}</span>
                    </span>
                  </>
                )}
                {j.state !== "uploading" ? (
                  <button type="button" className="btn btn-text" onClick={() => dismiss(j.key)}>
                    {t("common.dismiss")}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section aria-labelledby="library-title" className="section">
        <h2 id="library-title">{t("media.library")}</h2>
        <QueryStatus query={media} />
        {remove.isError && !blocked ? <ErrorAlert error={remove.error} /> : null}
        {blocked ? (
          <ErrorAlert error={blocked.error}>
            <p>{t("media.in_use.detail", { title: blocked.media.title, count: usedBy(blocked.error).length })}</p>
            <ul>
              {usedBy(blocked.error).map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </ErrorAlert>
        ) : null}
        {media.isSuccess && media.data.length === 0 ? <Empty icon="media">{t("media.empty")}</Empty> : null}
        <ul className="rows list-reset">
          {media.data?.map((m) => (
            <li key={m.id} className="row card" aria-labelledby={`media-${m.id}`}>
              <div className="row-main">
                <h3 id={`media-${m.id}`}>{m.title}</h3>
                <p className="meta">
                  {t(`media.kind.${m.kind}`)} · <span className="mono">{m.mime}</span> · {formatBytes(m.bytes, i18n.language)}
                </p>
                {m.status === "failed" ? (
                  <p className="field-error">
                    <Icon name="failed" />
                    <span>{errorText(m.error ?? "errors.unknown")}</span>
                  </p>
                ) : null}
              </div>
              <MediaStatus status={m.status} />
              <ConfirmAction
                label={t("media.delete.action")}
                confirmLabel={t("media.delete.confirm", { title: m.title })}
                pending={remove.isPending}
                onConfirm={() => remove.mutate(m)}
              />
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
