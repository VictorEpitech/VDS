import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useErrorText } from "../../auth/error-text";
import { ExpiryBadge, UnusedBadge } from "../../components/ContentBadges";
import { ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { MediaStatus } from "../../components/MediaStatus";
import { useApi } from "../../lib/api";
import { formatBytes, formatDateTime } from "../../lib/media-view";
import { keys, useMediaUsage, type Media } from "../../lib/queries";
import { useContentTypes } from "../../lib/content-types";
import { HiddenUsage, OwnerBadge, OwnerEdit } from "../scope/Owner";
import { useCanWrite, type NodeRef } from "../scope/scope";
import { ExpiryField } from "./ExpiryField";
import { MediaThumb } from "./MediaThumb";
import { TagEditor } from "./TagEditor";

type Detail = Media & { variants?: unknown[] };

/** Panneau de détail d'un média : aperçu, métadonnées, usage, remplacement, expiration, tags. */
export function MediaDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const api = useApi();
  const qc = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const contentTypes = useContentTypes();
  const [replacing, setReplacing] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [replaced, setReplaced] = useState(false);
  const detail = useQuery({ queryKey: [...keys.media, "one", id] as const, queryFn: () => api.get<Detail>(`/media/${id}`) });
  const usage = useMediaUsage(id);
  const media = detail.data;
  const canWrite = useCanWrite();
  const writable = canWrite(media?.owner);

  useEffect(() => {
    heading.current?.focus();
  }, [detail.isSuccess, id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const refresh = () => qc.invalidateQueries({ queryKey: keys.media });
  const patch = useMutation({
    mutationFn: (body: { expires_at?: string | null; tags?: string[]; owner?: NodeRef | null }) => api.patch<Media>(`/media/${id}`, body),
    onSuccess: refresh,
  });
  const replace = useMutation({
    mutationFn: (f: File) => api.upload<Media>(`/media/${id}/file`, f, {}, setProgress),
    onSuccess: async () => {
      setReplacing(false);
      setFile(null);
      setReplaced(true);
      await refresh();
    },
    onSettled: () => setProgress(null),
  });

  const lang = i18n.language;
  const accepted = contentTypes.acceptedMime.join(",");
  const playlists = usage.data?.playlists.length ?? 0;
  const screens = usage.data?.screens_now ?? 0;
  const dims = media?.width && media.height ? `${media.width} x ${media.height}` : null;
  const duration = media?.duration_ms ? t("media.detail.seconds", { count: Math.round(media.duration_ms / 1000) }) : null;

  return (
    <aside className="card media-detail" aria-labelledby="media-detail-title">
      <div className="page-head">
        <h2 id="media-detail-title" ref={heading} tabIndex={-1}>
          {media?.title ?? t("media.detail.title")}
        </h2>
        <button type="button" className="btn btn-text" onClick={onClose}>
          <Icon name="back" />
          {t("media.detail.close")}
        </button>
      </div>
      <QueryStatus query={detail} />
      {media ? (
        <>
          <MediaThumb media={media} size="large" />
          <div className="badges">
            <MediaStatus status={media.status} />
            <ExpiryBadge item={media} />
            {media.usage_count === 0 && media.status === "ready" ? <UnusedBadge /> : null}
            <OwnerBadge owner={media.owner} />
          </div>
          {media.status === "failed" ? (
            <p className="field-error">
              <Icon name="failed" />
              <span>{errorText(media.error ?? "errors.unknown")}</span>
            </p>
          ) : null}
          {replaced ? (
            <p className="field-help" role="status">
              {t("media.replace.done")}
            </p>
          ) : null}

          <dl className="facts">
            <dt>{t("media.detail.kind")}</dt>
            <dd>{t(`media.kind.${media.kind}`)}</dd>
            <dt>{t("media.detail.size")}</dt>
            <dd>{formatBytes(media.bytes, lang)}</dd>
            <dt>{t("media.detail.mime")}</dt>
            <dd className="mono">{media.mime}</dd>
            {dims ? (
              <>
                <dt>{t("media.detail.dimensions")}</dt>
                <dd className="mono">{dims}</dd>
              </>
            ) : null}
            {duration ? (
              <>
                <dt>{t("media.detail.duration")}</dt>
                <dd className="mono">{duration}</dd>
              </>
            ) : null}
            {media.page_count ? (
              <>
                <dt>{t("media.detail.pages")}</dt>
                <dd className="mono">{media.page_count}</dd>
              </>
            ) : null}
            <dt>{t("media.detail.added")}</dt>
            <dd>{formatDateTime(media.created_at, lang)}</dd>
          </dl>

          <section aria-labelledby="media-usage-title" className="section">
            <h3 id="media-usage-title">{t("media.usage.title")}</h3>
            <QueryStatus query={usage} />
            {usage.data ? (
              usage.data.playlists.length === 0 && usage.data.overrides.length === 0 ? (
                usage.data.hidden_count > 0 ? <HiddenUsage count={usage.data.hidden_count} /> : <p className="meta">{t("media.usage.none")}</p>
              ) : (
                <>
                  <ul className="list-reset usage-list">
                    {usage.data.playlists.map((p) => (
                      <li key={p.id}>
                        <Link to={`/content/playlists/${p.id}`}>{p.name}</Link> <ExpiryBadge item={p} />
                      </li>
                    ))}
                  </ul>
                  {usage.data.schedules.length > 0 ? (
                    <>
                      <h4>{t("media.usage.schedules")}</h4>
                      <ul className="list-reset usage-list">
                        {usage.data.schedules.map((s) => (
                          <li key={s.id}>{t("media.usage.schedule", { name: s.name, target: s.target_name })}</li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  {usage.data.overrides.length > 0 ? (
                    <>
                      <h4>{t("media.usage.overrides")}</h4>
                      <ul className="list-reset usage-list">
                        {usage.data.overrides.map((o) => (
                          <li key={o.id}>{t("media.usage.override", { name: o.name, date: formatDateTime(o.ends_at, lang) })}</li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  <HiddenUsage count={usage.data.hidden_count} />
                  <p className="meta">{t("media.usage.screens_now", { count: screens })}</p>
                </>
              )
            ) : null}
          </section>

          {writable ? (
          <section aria-labelledby="media-replace-title" className="section">
            <h3 id="media-replace-title">{t("media.replace.title")}</h3>
            {!replacing ? (
              <button
                type="button"
                className="btn btn-text"
                onClick={() => {
                  setReplacing(true);
                  setReplaced(false);
                }}
              >
                <Icon name="upload" />
                {t("media.replace.action")}
              </button>
            ) : (
              <div className="replace-box">
                <p role="status">{usage.isSuccess ? t("media.replace.impact", { playlists, screens }) : t("common.loading")}</p>
                <p className="field-help">{t("media.replace.help", { kind: t(`media.kind.${media.kind}`) })}</p>
                <input
                  ref={fileRef}
                  type="file"
                  accept={accepted}
                  className="visually-hidden"
                  aria-label={t("media.replace.input")}
                  tabIndex={-1}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    e.target.value = "";
                  }}
                />
                <div className="actions">
                  <button type="button" className="btn btn-text" onClick={() => fileRef.current?.click()}>
                    <Icon name="upload" />
                    {file ? t("media.replace.change", { name: file.name }) : t("media.replace.choose")}
                  </button>
                  <button type="button" className="btn btn-filled" disabled={!file || replace.isPending || !usage.isSuccess} onClick={() => file && replace.mutate(file)}>
                    {t("media.replace.confirm")}
                  </button>
                  <button type="button" className="btn btn-text" disabled={replace.isPending} onClick={() => { setReplacing(false); setFile(null); replace.reset(); }}>
                    {t("common.cancel")}
                  </button>
                </div>
                {progress !== null ? <progress max={100} value={Math.round(progress * 100)} aria-label={t("media.replace.progress")} /> : null}
                {replace.isError ? <ErrorAlert error={replace.error} /> : null}
              </div>
            )}
          </section>
          ) : null}

          {writable ? (
          <section aria-labelledby="media-options-title" className="section">
            <h3 id="media-options-title">{t("media.detail.options")}</h3>
            <ExpiryField key={`${media.id}-${media.expires_at ?? ""}`} value={media.expires_at} pending={patch.isPending} onSave={(iso) => patch.mutate({ expires_at: iso })} />
            <TagEditor scope="media" tags={media.tags} pending={patch.isPending} onChange={(tags) => patch.mutate({ tags })} />
            <OwnerEdit owner={media.owner} pending={patch.isPending} onSave={(owner) => patch.mutate({ owner })} />
            {patch.isError ? <ErrorAlert error={patch.error} /> : null}
            <details className="advanced">
              <summary>{t("form.advanced")}</summary>
              <div className="advanced-body">
                <dl className="facts">
                  <dt>{t("media.detail.filename")}</dt>
                  <dd className="mono">{media.original_filename ?? ""}</dd>
                  <dt>{t("media.detail.revision")}</dt>
                  <dd className="mono">{media.revision}</dd>
                  <dt>{t("media.detail.sha256")}</dt>
                  <dd className="mono">{media.sha256 ?? ""}</dd>
                </dl>
              </div>
            </details>
          </section>
          ) : null}
        </>
      ) : null}
    </aside>
  );
}
