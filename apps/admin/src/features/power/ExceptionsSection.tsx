import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { powerKeys, useAllGroups, useExceptions, useOrganization, useScreens, useSites, type PowerException } from "../../lib/queries";

type Level = PowerException["scope_level"];
const LEVELS: Level[] = ["organization", "site", "group", "screen"];

function ExceptionForm({ item, onDone }: { item?: PowerException; onDone: () => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const sites = useSites();
  const groups = useAllGroups();
  const screens = useScreens();
  const [label, setLabel] = useState(item?.label ?? "");
  const [level, setLevel] = useState<Level>(item?.scope_level ?? "site");
  const [scopeId, setScopeId] = useState(item?.scope_id ?? "");
  const [state, setState] = useState<"on" | "off">(item?.state ?? "off");
  const [startsOn, setStartsOn] = useState(item?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(item?.ends_on ?? "");
  const [start, setStart] = useState(item?.start ?? "");
  const [end, setEnd] = useState(item?.end ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const options =
    level === "site" ? (sites.data ?? []).filter((s) => !s.archived_at) : level === "group" ? (groups.data ?? []) : level === "screen" ? (screens.data ?? []).filter((s) => !s.archived_at) : [];
  const save = useMutation({
    mutationFn: (body: object) => (item ? api.put(`/power/exceptions/${item.id}`, body) : api.post("/power/exceptions", body)),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: powerKeys.exceptions });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!label.trim() || !startsOn || !endsOn || (level !== "organization" && !scopeId)) return setProblem("required");
    if (endsOn < startsOn) return setProblem("period");
    if ((start === "") !== (end === "")) return setProblem("window");
    if (start && end && start >= end) return setProblem("range");
    setProblem(null);
    save.mutate({ label: label.trim(), scope_level: level, scope_id: level === "organization" ? null : scopeId, state, starts_on: startsOn, ends_on: endsOn, start: start || null, end: end || null });
  };
  const title = item ? t("power.exceptions.edit") : t("power.exceptions.create");
  return (
    <form className="card" onSubmit={submit} noValidate aria-label={title}>
      <h3>{title}</h3>
      <div className="field">
        <label htmlFor={`${uid}-label`}>{t("power.exceptions.label")}</label>
        <input id={`${uid}-label`} value={label} maxLength={120} required autoComplete="off" onChange={(e) => setLabel(e.target.value)} />
      </div>
      <fieldset className="field">
        <legend>{t("power.exceptions.state")}</legend>
        <div className="seg" role="radiogroup" aria-label={t("power.exceptions.state")}>
          {(["off", "on"] as const).map((s) => (
            <label key={s} className="seg-item">
              <input type="radio" name={`${uid}-state`} value={s} checked={state === s} onChange={() => setState(s)} />
              <span>{t(`power.exceptions.states.${s}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="field">
        <label htmlFor={`${uid}-level`}>{t("power.scope.level")}</label>
        <select
          id={`${uid}-level`}
          value={level}
          onChange={(e) => {
            setLevel(e.target.value as Level);
            setScopeId("");
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
          <label htmlFor={`${uid}-scope`}>{t(`power.scope.${level}`)}</label>
          <select id={`${uid}-scope`} value={scopeId} required onChange={(e) => setScopeId(e.target.value)}>
            <option value="">{t("form.choose")}</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="times">
        <div className="field">
          <label htmlFor={`${uid}-from`}>{t("power.exceptions.from")}</label>
          <input id={`${uid}-from`} type="date" value={startsOn} required onChange={(e) => setStartsOn(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-to`}>{t("power.exceptions.to")}</label>
          <input id={`${uid}-to`} type="date" value={endsOn} required onChange={(e) => setEndsOn(e.target.value)} />
        </div>
      </div>
      <details className="advanced" open={Boolean(start || end)}>
        <summary>{t("form.advanced")}</summary>
        <div className="advanced-body">
          <p className="field-help">{t("power.exceptions.window_help")}</p>
          <div className="times">
            <label>
              <span>{t("schedules.rules.start")}</span>
              <input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              <span>{t("schedules.rules.end")}</span>
              <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
        </div>
      </details>
      {problem ? (
        <p className="field-error" role="alert">
          <Icon name="warning" />
          <span>{t(`power.exceptions.problem.${problem}`)}</span>
        </p>
      ) : null}
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-text" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

export function ExceptionsSection() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const items = useExceptions();
  const sites = useSites();
  const groups = useAllGroups();
  const screens = useScreens();
  const org = useOrganization();
  const [form, setForm] = useState<"new" | PowerException | null>(null);
  const remove = useMutation({
    mutationFn: (x: PowerException) => api.delete(`/power/exceptions/${x.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: powerKeys.exceptions }),
  });
  const scopeName = (x: PowerException): string => {
    if (x.scope_level === "organization") return org.data?.name ?? t("power.scope.organization");
    const list: { id: string; name: string }[] = x.scope_level === "site" ? (sites.data ?? []) : x.scope_level === "group" ? (groups.data ?? []) : (screens.data ?? []);
    return list.find((o) => o.id === x.scope_id)?.name ?? "";
  };
  const day = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
  return (
    <section className="section" aria-labelledby="exceptions-h">
      <div className="page-head">
        <h2 id="exceptions-h">
          <Icon name="schedule" />
          {t("power.exceptions.title")}
        </h2>
        <button type="button" className="btn btn-filled" disabled={form !== null} onClick={() => setForm("new")}>
          <Icon name="add" />
          {t("power.exceptions.create")}
        </button>
      </div>
      <p className="lead">{t("power.exceptions.intro")}</p>
      <QueryStatus query={items} />
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      {form ? <ExceptionForm key={form === "new" ? "new" : form.id} item={form === "new" ? undefined : form} onDone={() => setForm(null)} /> : null}
      {items.isSuccess && items.data.length === 0 ? <Empty icon="schedule">{t("power.exceptions.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {(items.data ?? []).map((x) => (
          <li key={x.id} className="card" aria-labelledby={`exc-${x.id}`}>
            <h3 id={`exc-${x.id}`}>
              <Icon name="schedule" />
              {x.label}
            </h3>
            <p>
              <StatusBadge state={x.state === "on" ? "online" : "scheduled_off"} icon={x.state === "on" ? "online" : "scheduled_off"} label={t(`power.exceptions.states.${x.state}`)} />
            </p>
            <p className="meta">
              {t(`power.scope.${x.scope_level}`)}
              {scopeName(x) ? ` : ${scopeName(x)}` : ""}
            </p>
            <p className="meta">
              {x.starts_on === x.ends_on ? day(x.starts_on) : t("power.exceptions.range", { from: day(x.starts_on), to: day(x.ends_on) })}
              {x.start && x.end ? ` · ${x.start}-${x.end}` : ""}
            </p>
            <div className="actions">
              <button type="button" className="btn btn-text" onClick={() => setForm(x)} aria-label={t("power.plans.edit_for", { name: x.label })}>
                {t("schedules.edit")}
              </button>
              <ConfirmAction label={t("schedules.delete.action")} confirmLabel={t("schedules.delete.confirm", { name: x.label })} pending={remove.isPending} onConfirm={() => remove.mutate(x)} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
