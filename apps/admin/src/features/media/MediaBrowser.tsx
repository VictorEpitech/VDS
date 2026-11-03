import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../../auth/error-text";
import { ExpiryBadge, UnusedBadge } from "../../components/ContentBadges";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { MediaStatus } from "../../components/MediaStatus";
import { ApiError, useApi } from "../../lib/api";
import { formatBytes, readView, storeView, type ViewMode } from "../../lib/media-view";
import { keys, useMediaPage, usePlaylists, type ListParams, type Media } from "../../lib/queries";
import { OwnerBadge } from "../scope/Owner";
import { useCanWrite } from "../scope/scope";
import { BulkBar } from "./BulkBar";
import { MediaDetail } from "./MediaDetail";
import { MediaThumb } from "./MediaThumb";
import { TagEditor } from "./TagEditor";

export const PAGE_SIZE = 24;
const SORTS = ["-created_at", "created_at", "title", "-title", "-bytes", "expires_at", "-updated_at"] as const;
const STATES = ["all", "active", "expiring", "expired"] as const;
const KINDS = ["image", "video", "document"] as const;

function Badges({ m }: { m: Media }) {
  return (
    <div className="badges">
      {m.status !== "ready" ? <MediaStatus status={m.status} /> : null}
      <ExpiryBadge item={m} />
      {m.usage_count === 0 && m.status === "ready" ? <UnusedBadge /> : null}
      <OwnerBadge owner={m.owner} />
    </div>
  );
}

/** Médiathèque : filtres, grille ou tableau, sélection multiple, panneau de détail. */
export function MediaBrowser({ onEmptyAction }: { onEmptyAction: () => void }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const [view, setView] = useState<ViewMode>(readView);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [state, setState] = useState<string>("all");
  const [tags, setTags] = useState<string[]>([]);
  const [unused, setUnused] = useState(false);
  const [sort, setSort] = useState<string>("-created_at");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Map<string, string>>(new Map());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<{ media: Media; error: ApiError } | null>(null);
  const params: ListParams = { q, kind, state, tags, unused, sort, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const media = useMediaPage(params);
  const playlists = usePlaylists();
  const canWrite = useCanWrite();
  const items = media.data?.items ?? [];
  const total = media.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = q.trim() !== "" || kind !== "" || state !== "all" || tags.length > 0 || unused;

  const remove = useMutation({
    mutationFn: (m: Media) => api.delete(`/media/${m.id}`),
    onMutate: () => setBlocked(null),
    onSuccess: (_r, m) => {
      setSelected((s) => {
        const n = new Map(s);
        n.delete(m.id);
        return n;
      });
      if (detailId === m.id) setDetailId(null);
      return qc.invalidateQueries({ queryKey: keys.media });
    },
    onError: (error, m) => {
      if (error instanceof ApiError && error.code === "media.in_use") setBlocked({ media: m, error });
    },
  });

  const usedBy = (err: ApiError): string[] => {
    const ids = Array.isArray(err.params.playlists) ? (err.params.playlists as string[]) : [];
    return ids.map((id) => playlists.data?.items.find((p) => p.id === id)?.name ?? "").filter(Boolean);
  };

  /** Tout changement de filtre ramène à la première page. */
  const filter = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(0);
  };
  const toggle = (m: Media) =>
    setSelected((s) => {
      const n = new Map(s);
      if (n.has(m.id)) n.delete(m.id);
      else n.set(m.id, m.title);
      return n;
    });
  const allOnPage = items.length > 0 && items.every((m) => selected.has(m.id));
  const togglePage = () =>
    setSelected((s) => {
      const n = new Map(s);
      for (const m of items) {
        if (allOnPage) n.delete(m.id);
        else n.set(m.id, m.title);
      }
      return n;
    });
  const open = (id: string) => setDetailId(id);
  const close = () => {
    const id = detailId;
    setDetailId(null);
    queueMicrotask(() => document.getElementById(`media-open-${id}`)?.focus());
  };
  const setViewMode = (v: ViewMode) => {
    setView(v);
    storeView(v);
  };

  const select = (m: Media) => (
    <input
      type="checkbox"
      className="media-check"
      checked={selected.has(m.id)}
      onChange={() => toggle(m)}
      aria-label={t("media.select", { title: m.title })}
    />
  );
  const title = (m: Media) => (
    <button type="button" id={`media-open-${m.id}`} className="media-title" aria-expanded={detailId === m.id} onClick={() => open(m.id)}>
      {m.title}
    </button>
  );
  // Action d'écriture masquée (non désactivée) quand l'objet n'est pas modifiable par ce périmètre.
  const del = (m: Media) =>
    !canWrite(m.owner) ? null : (
    <ConfirmAction label={t("media.delete.action")} confirmLabel={t("media.delete.confirm", { title: m.title })} pending={remove.isPending} onConfirm={() => remove.mutate(m)} />
    );

  return (
    <section aria-labelledby="library-title" className="section">
      <div className="page-head">
        <h2 id="library-title">{t("media.library")}</h2>
        <div className="actions" role="group" aria-label={t("media.view.label")}>
          <button type="button" className="btn btn-text" aria-pressed={view === "grid"} onClick={() => setViewMode("grid")}>
            <Icon name="media" />
            {t("media.view.grid")}
          </button>
          <button type="button" className="btn btn-text" aria-pressed={view === "table"} onClick={() => setViewMode("table")}>
            <Icon name="schedule" />
            {t("media.view.table")}
          </button>
        </div>
      </div>

      <div className="filters" role="search" aria-label={t("media.filters.label")}>
        <div className="field">
          <label htmlFor={`${uid}-q`}>{t("media.filters.search")}</label>
          <input id={`${uid}-q`} type="search" value={q} onChange={(e) => filter(setQ)(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-kind`}>{t("media.filters.kind")}</label>
          <select id={`${uid}-kind`} value={kind} onChange={(e) => filter(setKind)(e.target.value)}>
            <option value="">{t("media.filters.all_kinds")}</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`media.kind.${k}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${uid}-state`}>{t("media.filters.state")}</label>
          <select id={`${uid}-state`} value={state} onChange={(e) => filter(setState)(e.target.value)}>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {t(`media.filters.states.${s}`)}
              </option>
            ))}
          </select>
        </div>
        <details className="advanced filters-advanced">
          <summary>{t("form.advanced")}</summary>
          <div className="advanced-body">
            <div className="field">
              <label htmlFor={`${uid}-sort`}>{t("media.filters.sort")}</label>
              <select id={`${uid}-sort`} value={sort} onChange={(e) => filter(setSort)(e.target.value)}>
                {SORTS.map((s) => (
                  <option key={s} value={s}>
                    {t(`media.filters.sorts.${s}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field field-check">
              <input id={`${uid}-unused`} type="checkbox" checked={unused} onChange={(e) => filter(setUnused)(e.target.checked)} />
              <label htmlFor={`${uid}-unused`}>{t("media.filters.unused")}</label>
            </div>
            <TagEditor scope="media" tags={tags} onChange={filter(setTags)} label={t("media.filters.tags")} />
          </div>
        </details>
      </div>

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

      <BulkBar
        selected={selected}
        onClear={() => setSelected(new Map())}
        onResult={(r) => {
          setSelected((s) => {
            const n = new Map(s);
            for (const id of r.done) n.delete(id);
            return n;
          });
          void qc.invalidateQueries({ queryKey: keys.media });
        }}
      />

      {media.isSuccess && total === 0 ? (
        filtered ? (
          <Empty icon="media">{t("media.no_match")}</Empty>
        ) : (
          <Empty
            icon="media"
            action={
              <button type="button" className="btn btn-filled" onClick={onEmptyAction}>
                <Icon name="upload" />
                {t("media.empty_action")}
              </button>
            }
          >
            {t("media.empty")}
          </Empty>
        )
      ) : null}

      <div className={detailId ? "media-layout media-layout-open" : "media-layout"}>
        <div className="media-main">
          {items.length > 0 ? (
            <div className="field field-check">
              <input id={`${uid}-all`} type="checkbox" checked={allOnPage} onChange={togglePage} />
              <label htmlFor={`${uid}-all`}>{t("media.select_page")}</label>
            </div>
          ) : null}

          {view === "grid" ? (
            <ul className="media-grid list-reset" aria-label={t("media.library")}>
              {items.map((m) => (
                <li key={m.id} className="card media-card" data-selected={selected.has(m.id) || undefined}>
                  <div className="media-card-head">
                    {select(m)}
                    <MediaThumb media={m} />
                  </div>
                  {title(m)}
                  <p className="meta">
                    {t(`media.kind.${m.kind}`)} · {formatBytes(m.bytes, i18n.language)}
                  </p>
                  <Badges m={m} />
                  {m.status === "failed" ? (
                    <p className="field-error">
                      <Icon name="failed" />
                      <span>{errorText(m.error ?? "errors.unknown")}</span>
                    </p>
                  ) : null}
                  {del(m)}
                </li>
              ))}
            </ul>
          ) : items.length > 0 ? (
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- région défilante : focus clavier requis (axe)
            <div className="table-scroll" role="region" tabIndex={0} aria-label={t("media.library")}>
              <table className="media-table">
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="visually-hidden">{t("media.col.select")}</span>
                    </th>
                    <th scope="col">{t("media.col.title")}</th>
                    <th scope="col">{t("media.col.kind")}</th>
                    <th scope="col">{t("media.col.size")}</th>
                    <th scope="col">{t("media.col.state")}</th>
                    <th scope="col">{t("media.col.usage")}</th>
                    <th scope="col">
                      <span className="visually-hidden">{t("media.col.actions")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((m) => (
                    <tr key={m.id} data-selected={selected.has(m.id) || undefined}>
                      <td>{select(m)}</td>
                      <th scope="row">
                        <span className="media-cell">
                          <MediaThumb media={m} size="small" />
                          {title(m)}
                        </span>
                      </th>
                      <td>{t(`media.kind.${m.kind}`)}</td>
                      <td>{formatBytes(m.bytes, i18n.language)}</td>
                      <td>
                        <Badges m={m} />
                        {m.status === "failed" ? <span className="meta">{errorText(m.error ?? "errors.unknown")}</span> : null}
                      </td>
                      <td>{t("media.usage_count", { count: m.usage_count })}</td>
                      <td>{del(m)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {total > PAGE_SIZE ? (
            <nav className="pager" aria-label={t("media.pager.label")}>
              <button type="button" className="btn btn-text" disabled={page === 0} onClick={() => setPage(page - 1)}>
                <Icon name="back" />
                {t("media.pager.previous")}
              </button>
              <span role="status">{t("media.pager.page", { page: page + 1, pages, total })}</span>
              <button type="button" className="btn btn-text" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
                {t("media.pager.next")}
              </button>
            </nav>
          ) : null}
        </div>
        {detailId ? <MediaDetail key={detailId} id={detailId} onClose={close} /> : null}
      </div>
    </section>
  );
}
