import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { keys, useAllGroups, useSites, type Screen } from "../../lib/queries";

type Orientation = "landscape" | "portrait";

/** Création d'un écran (POST /screens) ; propose ensuite l'appairage avec l'écran présélectionné. */
export function ScreenCreateForm({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const sites = useSites();
  const groups = useAllGroups();
  const [name, setName] = useState("");
  const [siteId, setSiteId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [orientation, setOrientation] = useState<Orientation>("landscape");
  const [missingSite, setMissingSite] = useState(false);
  const create = useMutation({
    mutationFn: (b: { name: string; site_id: string; group_id: string | null; orientation: Orientation }) => api.post<Screen>("/screens", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.screens }),
  });
  const activeSites = (sites.data ?? []).filter((s) => !s.archived_at);
  const siteGroups = (groups.data ?? []).filter((g) => g.site_id === siteId);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setMissingSite(siteId === "");
    if (!name.trim() || !siteId) return;
    create.mutate({ name: name.trim(), site_id: siteId, group_id: groupId || null, orientation });
  };

  if (create.isSuccess) {
    return (
      <div className="card inline-form">
        <SuccessAlert>{t("screens.create.done", { name: create.data.name })}</SuccessAlert>
        <div className="actions">
          <Link className="btn btn-filled" to={`/pairing?screen=${create.data.id}`}>
            <Icon name="pairing" />
            {t("screens.create.pair_now")}
          </Link>
          <button type="button" className="btn btn-text" onClick={onClose}>
            {t("common.dismiss")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="card inline-form" onSubmit={submit} aria-label={t("screens.create.title")} noValidate>
      <h2>{t("screens.create.title")}</h2>
      <div className="field">
        <label htmlFor={`${uid}-name`}>{t("screens.create.name")}</label>
        <input id={`${uid}-name`} value={name} maxLength={120} required autoComplete="off" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-site`}>{t("screens.create.site")}</label>
        <select
          id={`${uid}-site`}
          value={siteId}
          required
          aria-invalid={missingSite ? true : undefined}
          onChange={(e) => {
            setSiteId(e.target.value);
            setGroupId("");
          }}
        >
          <option value="">{t("form.choose")}</option>
          {activeSites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        {missingSite ? (
          <p className="field-error">
            <Icon name="warning" />
            <span>{t("errors.field_required")}</span>
          </p>
        ) : null}
      </div>
      <div className="field">
        <label htmlFor={`${uid}-group`}>{t("screens.create.group")}</label>
        <select id={`${uid}-group`} value={groupId} disabled={!siteId} onChange={(e) => setGroupId(e.target.value)}>
          <option value="">{t("screens.create.no_group")}</option>
          {siteGroups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-orientation`}>{t("screens.create.orientation")}</label>
        <select id={`${uid}-orientation`} value={orientation} onChange={(e) => setOrientation(e.target.value as Orientation)}>
          <option value="landscape">{t("screens.orientation.landscape")}</option>
          <option value="portrait">{t("screens.orientation.portrait")}</option>
        </select>
      </div>
      {activeSites.length === 0 && sites.isSuccess ? (
        <p className="meta">
          {t("screens.create.no_site")} <Link to="/sites">{t("screens.create.go_sites")}</Link>
        </p>
      ) : null}
      {create.isError ? <ErrorAlert error={create.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={create.isPending} aria-busy={create.isPending}>
          <Icon name="online" />
          {t("screens.create.submit")}
        </button>
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
