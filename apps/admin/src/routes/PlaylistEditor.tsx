import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { ItemForm, type ItemDraft } from "../features/playlist/ItemForm";
import { useApi } from "../lib/api";
import { useContentTypes } from "../lib/content-types";
import { keys, useMedia, usePlaylist, type PlaylistDetail, type PlaylistItem } from "../lib/queries";

export function PlaylistEditor() {
  const { id = "" } = useParams();
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const playlist = usePlaylist(id);
  const media = useMedia();
  const contentTypes = useContentTypes();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const focusAfter = useRef<string | null>(null);
  const lang = i18n.language === "en" ? "en" : "fr";
  const items = playlist.data?.items ?? [];

  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: keys.playlist(id) }), qc.invalidateQueries({ queryKey: keys.playlists })]);

  const rename = useMutation({
    mutationFn: (name: string) => api.patch(`/playlists/${id}`, { name }),
    onSuccess: refresh,
  });
  const add = useMutation({
    mutationFn: (d: ItemDraft) => api.post(`/playlists/${id}/items`, { content_type: d.content_type, props: d.props, duration_ms: d.duration_ms }),
    onSuccess: async () => {
      setAdding(false);
      await refresh();
    },
  });
  const edit = useMutation({
    mutationFn: ({ item, d }: { item: PlaylistItem; d: ItemDraft }) =>
      api.patch(`/playlists/${id}/items/${item.id}`, { props: d.props, duration_ms: d.duration_ms, enabled: d.enabled }),
    onSuccess: async () => {
      setEditing(null);
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (item: PlaylistItem) => api.delete(`/playlists/${id}/items/${item.id}`),
    onSuccess: refresh,
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.put<PlaylistDetail>(`/playlists/${id}/items/order`, { item_ids: ids }),
    onMutate: async (ids) => {
      await qc.cancelQueries({ queryKey: keys.playlist(id) });
      const before = qc.getQueryData<PlaylistDetail>(keys.playlist(id));
      if (before) qc.setQueryData<PlaylistDetail>(keys.playlist(id), { ...before, items: ids.map((i) => before.items.find((x) => x.id === i)!) });
      return { before };
    },
    onError: (_e, _ids, ctx) => {
      if (ctx?.before) qc.setQueryData(keys.playlist(id), ctx.before);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.playlist(id) }),
  });

  const move = (index: number, dir: -1 | 1) => {
    const ids = items.map((i) => i.id);
    const target = index + dir;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target]!, ids[index]!];
    focusAfter.current = `move-${dir === -1 ? "up" : "down"}-${ids[target]}`;
    setAnnounce(t("playlist.moved", { name: label(items[index]!), position: target + 1, total: ids.length }));
    reorder.mutate(ids);
  };

  // Le focus clavier suit l'élément déplacé (ou l'autre bouton s'il est en bout de liste), une fois l'ordre à jour.
  const order = items.map((i) => i.id).join();
  useEffect(() => {
    const wanted = focusAfter.current;
    if (!wanted) return;
    const first = document.getElementById(wanted) as HTMLButtonElement | null;
    const other = document.getElementById(wanted.replace(/-(up|down)-/, (_m, d: string) => (d === "up" ? "-down-" : "-up-"))) as HTMLButtonElement | null;
    (first && !first.disabled ? first : other)?.focus();
    focusAfter.current = null;
  }, [order]);

  const mediaTitle = (mid: string) => media.data?.find((m) => m.id === mid)?.title ?? "";
  const summary = (item: PlaylistItem): string => {
    const type = contentTypes.byId(item.content_type);
    for (const [name, field] of Object.entries(type?.schema.properties ?? {})) {
      const v = item.props[name];
      if (typeof v !== "string") continue;
      return field["x-vds-media"] ? mediaTitle(v) : v;
    }
    return "";
  };
  const label = (item: PlaylistItem) => {
    const type = contentTypes.byId(item.content_type);
    return [type?.name[lang] ?? item.content_type, summary(item)].filter(Boolean).join(" - ");
  };

  const [name, setName] = useState<string | null>(null);
  const nameValue = name ?? playlist.data?.name ?? "";

  return (
    <>
      <p>
        <Link to="/playlists">{t("playlist.back")}</Link>
      </p>
      <h1>{playlist.data?.name ?? t("playlist.title")}</h1>
      <QueryStatus query={playlist} />
      {playlist.isSuccess ? (
        <>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (nameValue.trim()) rename.mutate(nameValue.trim());
            }}
          >
            <div className="field">
              <label htmlFor="playlist-name">{t("playlists.name")}</label>
              <input id="playlist-name" value={nameValue} maxLength={120} onChange={(e) => setName(e.target.value)} />
            </div>
            <button type="submit" className="btn btn-text" disabled={rename.isPending || nameValue.trim() === playlist.data.name}>
              {t("common.save")}
            </button>
          </form>
          {rename.isError ? <ErrorAlert error={rename.error} /> : null}

          <section className="section" aria-labelledby="items-title">
            <div className="page-head">
              <h2 id="items-title">{t("playlist.items")}</h2>
              {!adding ? (
                <button type="button" className="btn btn-filled" onClick={() => { setAdding(true); setEditing(null); }}>
                  <Icon name="add" />
                  {t("playlist.item.add")}
                </button>
              ) : null}
            </div>
            <p className="visually-hidden" role="status">
              {announce}
            </p>
            {remove.isError ? <ErrorAlert error={remove.error} /> : null}
            {reorder.isError ? <ErrorAlert error={reorder.error} /> : null}
            {items.length === 0 ? <Empty icon="playlist">{t("playlist.empty")}</Empty> : null}
            <ol className="rows list-reset">
              {items.map((item, index) => (
                <li key={item.id} className="item-row">
                  <div className="row card">
                    <span className="position" aria-hidden="true">
                      {index + 1}
                    </span>
                    <div className="row-main">
                      <h3>{label(item)}</h3>
                      <p className="meta">
                        {item.duration_ms ? t("playlist.item.seconds", { count: item.duration_ms / 1000 }) : t("playlist.item.duration_default")}
                        {item.enabled ? null : ` · ${t("playlist.item.disabled")}`}
                      </p>
                    </div>
                    <div className="actions">
                      <button
                        type="button"
                        id={`move-up-${item.id}`}
                        className="btn btn-text btn-icon"
                        disabled={index === 0}
                        aria-label={t("playlist.move_up", { name: label(item) })}
                        onClick={() => move(index, -1)}
                      >
                        <Icon name="move_up" />
                      </button>
                      <button
                        type="button"
                        id={`move-down-${item.id}`}
                        className="btn btn-text btn-icon"
                        disabled={index === items.length - 1}
                        aria-label={t("playlist.move_down", { name: label(item) })}
                        onClick={() => move(index, 1)}
                      >
                        <Icon name="move_down" />
                      </button>
                      <button type="button" className="btn btn-text" onClick={() => { setEditing(editing === item.id ? null : item.id); setAdding(false); }} aria-expanded={editing === item.id}>
                        {t("playlist.item.edit")}
                      </button>
                      <ConfirmAction
                        label={t("playlist.item.delete.action")}
                        confirmLabel={t("playlist.item.delete.confirm")}
                        pending={remove.isPending}
                        onConfirm={() => remove.mutate(item)}
                      />
                    </div>
                  </div>
                  {editing === item.id ? (
                    <ItemForm
                      item={item}
                      media={media.data ?? []}
                      pending={edit.isPending}
                      error={edit.error}
                      onSubmit={(d) => edit.mutate({ item, d })}
                      onCancel={() => setEditing(null)}
                    />
                  ) : null}
                </li>
              ))}
            </ol>
            {adding ? (
              <ItemForm media={media.data ?? []} pending={add.isPending} error={add.error} onSubmit={(d) => add.mutate(d)} onCancel={() => setAdding(false)} />
            ) : null}
          </section>
        </>
      ) : null}
    </>
  );
}
