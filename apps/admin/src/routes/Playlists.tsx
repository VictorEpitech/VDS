import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { useApi } from "../lib/api";
import { keys, usePlaylists, type Playlist, type PlaylistDetail } from "../lib/queries";

export function Playlists() {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const playlists = usePlaylists();
  const [name, setName] = useState("");
  const [missing, setMissing] = useState(false);
  const create = useMutation({
    mutationFn: (n: string) => api.post<PlaylistDetail>("/playlists", { name: n }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: keys.playlists });
      navigate(`/playlists/${p.id}`);
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
      <h1>{t("playlists.title")}</h1>
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
        {create.isError ? <ErrorAlert error={create.error} /> : null}
        <button type="submit" className="btn btn-filled" disabled={create.isPending}>
          <Icon name="add" />
          {t("playlists.create")}
        </button>
      </form>
      <section className="section" aria-labelledby="pl-list">
        <h2 id="pl-list">{t("playlists.list")}</h2>
        <QueryStatus query={playlists} />
        {remove.isError ? <ErrorAlert error={remove.error} /> : null}
        {playlists.isSuccess && playlists.data.length === 0 ? <Empty icon="playlist">{t("playlists.empty")}</Empty> : null}
        <ul className="rows list-reset">
          {playlists.data?.map((p) => (
            <li key={p.id} className="row card">
              <div className="row-main">
                <h3>
                  <Link to={`/playlists/${p.id}`}>{p.name}</Link>
                </h3>
                <p className="meta">{t("playlists.items", { count: p.item_count })}</p>
              </div>
              <ConfirmAction
                label={t("playlists.delete.action")}
                confirmLabel={t("playlists.delete.confirm", { name: p.name })}
                pending={remove.isPending}
                onConfirm={() => remove.mutate(p)}
              />
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
