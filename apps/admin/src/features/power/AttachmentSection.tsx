import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { configKey, powerKeys, useAllGroups, useCalendars, useConfig, useOrganization, usePlans, useScreens, useSites, type ScreenPower } from "../../lib/queries";
import { calendarName } from "./format";

type Level = "organization" | "site" | "group" | "screen";
const LEVELS: Level[] = ["organization", "site", "group", "screen"];

/** Créneaux résolus d'un écran (lecture seule) : ce que l'écran fera réellement. */
function ResolvedSlots({ screenId }: { screenId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const q = useQuery({ queryKey: powerKeys.screen(screenId), queryFn: () => api.get<ScreenPower>(`/power/screens/${screenId}`) });
  if (!q.data) return <QueryStatus query={q} />;
  const fmt = (iso: string) => new Intl.DateTimeFormat(i18n.language, { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: q.data.timezone }).format(new Date(iso));
  return (
    <div>
      <h4>{t("power.attachment.resolved")}</h4>
      {q.data.slots.length === 0 ? (
        <p className="meta">{t("power.attachment.no_slots")}</p>
      ) : (
        <ul className="list-reset rows">
          {q.data.slots.slice(0, 8).map((s) => (
            <li key={`${s.from}-${s.to}`} className="row-line">
              <span>
                {fmt(s.from)} → {fmt(s.to)}
              </span>
              <span className="meta">{t(`power.exceptions.states.${s.state}`)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Plan et calendriers d'un niveau : valeur résolue, provenance, « revenir à l'héritage ». */
export function AttachmentPanel({ level, id }: { level: Level; id: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const cfg = useConfig(level, id, "core");
  const plans = usePlans();
  const cals = useCalendars();
  const [saved, setSaved] = useState(false);
  const done = async () => {
    setSaved(true);
    await qc.invalidateQueries({ queryKey: configKey(level, id, "core") });
    await qc.invalidateQueries({ queryKey: ["power", "screen"] });
  };
  const put = useMutation({
    mutationFn: (v: { key: string; value: unknown }) => api.put(`/config/${level}/${id}/core/${v.key}`, { value: v.value }),
    onMutate: () => setSaved(false),
    onSuccess: done,
  });
  const revert = useMutation({
    mutationFn: (key: string) => api.delete(`/config/${level}/${id}/core/${key}`),
    onMutate: () => setSaved(false),
    onSuccess: done,
  });
  if (!cfg.data) return <QueryStatus query={cfg} />;
  const planV = cfg.data.values["power.plan_id"];
  const calV = cfg.data.values["power.calendars"];
  const planId = typeof planV?.value === "string" ? planV.value : "";
  const selected = Array.isArray(calV?.value) ? (calV.value as string[]) : [];
  const toggle = (key: string) => put.mutate({ key: "power.calendars", value: selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key] });
  return (
    <div className="attachment">
      {put.isError ? <ErrorAlert error={put.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("power.attachment.saved")}</SuccessAlert> : null}
      {planV ? (
        <div className="field">
          <label htmlFor={`${uid}-plan`}>{t("power.attachment.plan")}</label>
          <select id={`${uid}-plan`} value={planId} disabled={put.isPending} onChange={(e) => put.mutate({ key: "power.plan_id", value: e.target.value || null })}>
            <option value="">{t("power.attachment.no_plan")}</option>
            {(plans.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <div className="provenance-row">
            <ProvenanceBadge origin={planV.origin} level={level} />
            <RevertButton origin={planV.origin} level={level} field={t("power.attachment.plan")} pending={revert.isPending} onRevert={() => revert.mutate("power.plan_id")} />
          </div>
        </div>
      ) : null}
      {calV ? (
        <fieldset className="field">
          <legend>{t("power.attachment.calendars")}</legend>
          {(cals.data ?? []).length === 0 ? <p className="field-help">{t("power.calendars.empty")}</p> : null}
          {(cals.data ?? []).map((c) => (
            <div className="field-check" key={c.key}>
              <label>
                <input type="checkbox" checked={selected.includes(c.key)} disabled={put.isPending} onChange={() => toggle(c.key)} />
                <span>{calendarName(c.name, i18n.language, c.key)}</span>
              </label>
            </div>
          ))}
          <div className="provenance-row">
            <ProvenanceBadge origin={calV.origin} level={level} />
            <RevertButton origin={calV.origin} level={level} field={t("power.attachment.calendars")} pending={revert.isPending} onRevert={() => revert.mutate("power.calendars")} />
          </div>
        </fieldset>
      ) : null}
      {level === "screen" ? <ResolvedSlots screenId={id} /> : null}
    </div>
  );
}

/** Choix d'un niveau et d'une cible, puis rattachement du plan. */
export function AttachmentSection() {
  const { t } = useTranslation();
  const uid = useId();
  const org = useOrganization();
  const sites = useSites();
  const groups = useAllGroups();
  const screens = useScreens();
  const [level, setLevel] = useState<Level>("site");
  const [target, setTarget] = useState("");
  const options =
    level === "site" ? (sites.data ?? []).filter((s) => !s.archived_at) : level === "group" ? (groups.data ?? []) : level === "screen" ? (screens.data ?? []).filter((s) => !s.archived_at) : [];
  const id = level === "organization" ? (org.data?.id ?? "") : target;
  return (
    <section className="section" aria-labelledby="attachment-h">
      <h2 id="attachment-h">
        <Icon name="inherit" />
        {t("power.attachment.title")}
      </h2>
      <p className="lead">{t("power.attachment.intro")}</p>
      <div className="card">
        <div className="field">
          <label htmlFor={`${uid}-level`}>{t("power.scope.level")}</label>
          <select
            id={`${uid}-level`}
            value={level}
            onChange={(e) => {
              setLevel(e.target.value as Level);
              setTarget("");
            }}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {t(`power.scope.${l}`)}
              </option>
            ))}
          </select>
        </div>
        {level !== "organization" ? (
          <div className="field">
            <label htmlFor={`${uid}-target`}>{t(`power.scope.${level}`)}</label>
            <select id={`${uid}-target`} value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">{t("form.choose")}</option>
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {id ? <AttachmentPanel key={`${level}-${id}`} level={level} id={id} /> : <p className="meta">{t("power.attachment.pick")}</p>}
      </div>
    </section>
  );
}
