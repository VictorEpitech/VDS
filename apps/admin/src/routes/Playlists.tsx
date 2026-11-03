import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { ExpiryBadge } from "../components/ContentBadges";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { useApi } from "../lib/api";
import { TagEditor } from "../features/media/TagEditor";
import { OwnerBadge, OwnerField, useOwnerChoice } from "../features/scope/Owner";
import { useCanWrite } from "../features/scope/scope";
import { keys, usePlaylistPage, type Playlist, type PlaylistDetail } from "../lib/queries";
import { PublicationBadge } from "../features/publication/PublicationBadge";
import { usePublicationIndex } from "../features/publication/publication";
import { PageTitle } from "../features/help/HelpPanel";

const PAGE_SIZE = 24;
const STATES = ["all", "active", "expiring", "expired"] as const;

export function Playlists() {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [stateFilter, setStateFilter] = useState<string>("all");
  const [tags, setTags] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const playlists = usePlaylistPage({ q, state: stateFilter, tags, sort: "-updated_at", limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const total = playlists.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = q.trim() !== "" || stateFilter !== "all" || tags.length > 0;
  const pubs = usePublicationIndex("playlist");
  const owner = useOwnerChoice();
  const canWrite = useCanWrite();
  const [name, setName] = useState("");
  const [missing, setMissing] = useState(false);
  const create = useMutation({
    mutationFn: (n: string) => api.post<PlaylistDetail>("/playlists", owner.owner === undefined ? { name: n } : { name: n, owner: owner.owner }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: keys.playlists });
      navigate(`/content/playlists/${p.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: (p: Playlist) => api.delete(`/playlists/${p.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.playlists }),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    setMissing(n === "");
    if (n) create.mutate(n);
  };

  return (
    <>
      <PageTitle topic="playlists">{t("playlists.title")}</PageTitle>
      <p className="lead">{t("playlists.intro")}</p>
      <form className="card form-narrow" onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor={`${uid}-name`}>{t("playlists.name")}</label>
          <input
            id={`${uid}-name`}
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            required
            aria-invalid={missing ? true : undefined}
          />
          {missing ? (
            <p className="field-error">
              <Icon name="warning" />
              <span>{t("errors.field_required")}</span>
            </p>
          ) : null}
        </div>
        <OwnerField choice={owner} />
        {create.isError ? <ErrorAlert error={create.error} /> : null}
        <button type="submit" className="btn btn-filled" disabled={create.isPending}>
          <Icon name="add" />
          {t("playlists.create")}
        </button>
      </form>
      <section className="section" aria-labelledby="pl-list">
        <h2 id="pl-list">{t("playlists.list")}</h2>
        <div className="filters" role="search" aria-label={t("playlists.filters.label")}>
          <div className="field">
            <label htmlFor={`${uid}-q`}>{t("playlists.filters.search")}</label>
            <input id={`${uid}-q`} type="search" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-state`}>{t("playlists.filters.state")}</label>
            <select id={`${uid}-state`} value={stateFilter} onChange={(e) => { setStateFilter(e.target.value); setPage(0); }}>
              {STATES.map((st) => (
                <option key={st} value={st}>
                  {t(`media.filters.states.${st}`)}
                </option>
              ))}
            </select>
          </div>
          <details className="advanced filters-advanced">
            <summary>{t("form.advanced")}</summary>
            <div className="advanced-body">
              <TagEditor scope="playlists" tags={tags} onChange={(v) => { setTags(v); setPage(0); }} label={t("playlists.filters.tags")} />
            </div>
          </details>
        </div>
        <QueryStatus query={playlists} />
        {remove.isError ? <ErrorAlert error={remove.error} /> : null}
        {playlists.isSuccess && total === 0 && filtered ? <Empty icon="playlist">{t("playlists.no_match")}</Empty> : null}
        {playlists.isSuccess && total === 0 && !filtered ? (
          <Empty
            icon="playlist"
            action={
              <button type="button" className="btn btn-filled" onClick={() => document.getElementById(`${uid}-name`)?.focus()}>
                <Icon name="add" />
                {t("playlists.empty_action")}
              </button>
            }
          >
            {t("playlists.empty")}
          </Empty>
        ) : null}
        <ul className="rows list-reset">
          {playlists.data?.items.map((p) => (
            <li key={p.id} className="row card">
              <div className="row-main">
                <h3>
                  <Link to={`/content/playlists/${p.id}`}>{p.name}</Link>
                </h3>
                <p className="meta">{t("playlists.items", { count: p.item_count })}</p>
                {p.tags.length > 0 ? <p className="meta">{t("tags.list", { tags: p.tags.join(", ") })}</p> : null}
                <div className="badges">
                  <PublicationBadge publication={pubs.get(p.id)} />
                  <ExpiryBadge item={p} />
                  <OwnerBadge owner={p.owner} />
                </div>
              </div>
              {canWrite(p.owner) ? (
                <ConfirmAction
                  label={t("playlists.delete.action")}
                  confirmLabel={t("playlists.delete.confirm", { name: p.name })}
                  pending={remove.isPending}
                  onConfirm={() => remove.mutate(p)}
                />
              ) : null}
            </li>
          ))}
        </ul>
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
      </section>
    </>
  );
}
