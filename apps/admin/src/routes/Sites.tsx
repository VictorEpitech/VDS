import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { AgentStatusBadge } from "../features/sites/AgentsPanel";
import { useApi } from "../lib/api";
import { keys, useAllAgents, useScreens, useSites, type Site } from "../lib/queries";

export const slugify = (v: string) =>
  v
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);

export function Sites() {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const sites = useSites();
  const screens = useScreens();
  const agents = useAllAgents();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const create = useMutation({
    mutationFn: (b: { name: string; slug: string }) => api.post<Site>("/sites", b),
    onSuccess: async () => {
      setOpen(false);
      setName("");
      setSlug("");
      setSlugTouched(false);
      await qc.invalidateQueries({ queryKey: keys.sites });
    },
  });
  const active = (sites.data ?? []).filter((s) => !s.archived_at);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() && slug) create.mutate({ name: name.trim(), slug });
  };

  return (
    <>
      <div className="page-head">
        <h1>{t("sites.title")}</h1>
        <button type="button" className="btn btn-filled" onClick={() => setOpen(true)} disabled={open}>
          <Icon name="add" />
          {t("sites.create.action")}
        </button>
      </div>
      <p className="lead">{t("sites.intro")}</p>
      {open ? (
        <form className="card inline-form" onSubmit={submit} aria-label={t("sites.create.title")}>
          <h2>{t("sites.create.title")}</h2>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("sites.name")}</label>
            <input
              id={`${uid}-name`}
              value={name}
              maxLength={120}
              required
              autoComplete="off"
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-slug`}>{t("sites.slug")}</label>
            <input
              id={`${uid}-slug`}
              className="mono"
              value={slug}
              maxLength={63}
              required
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={`${uid}-slug-h`}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(e.target.value);
              }}
            />
            <p className="field-help" id={`${uid}-slug-h`}>
              {t("sites.slug_help")}
            </p>
          </div>
          {create.isError ? <ErrorAlert error={create.error} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={create.isPending} aria-busy={create.isPending}>
              <Icon name="online" />
              {t("common.save")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
      <QueryStatus query={sites} />
      {sites.isSuccess && active.length === 0 ? <Empty icon="site">{t("sites.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {active.map((s) => {
          const agent = (agents.data ?? []).find((a) => a.site_id === s.id && a.status !== "revoked");
          const count = (screens.data ?? []).filter((x) => x.site_id === s.id && !x.archived_at).length;
          return (
            <li key={s.id} className="card" aria-labelledby={`site-${s.id}`}>
              <h2 id={`site-${s.id}`}>
                <Icon name="site" />
                {s.name}
              </h2>
              <p className="meta">
                <code>{s.slug}</code> · {t("sites.screens_count", { count })}
              </p>
              <p>{agent ? <AgentStatusBadge status={agent.status} /> : <span className="meta">{t("sites.no_agent")}</span>}</p>
              <div className="actions">
                <Link className="btn btn-text" to={`/sites/${s.id}`} aria-label={t("sites.manage_for", { name: s.name })}>
                  <Icon name="agent" />
                  {t("sites.manage")}
                </Link>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
