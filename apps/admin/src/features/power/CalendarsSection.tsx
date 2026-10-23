import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { powerKeys, useCalendars, type Calendar, type CalendarDay } from "../../lib/queries";
import { calendarName } from "./format";

function Days({ cal }: { cal: Calendar }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const days = useQuery({ queryKey: powerKeys.days(cal.key), queryFn: () => api.get<CalendarDay[]>(`/power/calendars/${cal.key}/days`) });
  const [day, setDay] = useState("");
  const [label, setLabel] = useState("");
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: powerKeys.days(cal.key) });
    await qc.invalidateQueries({ queryKey: powerKeys.calendars });
  };
  const add = useMutation({
    mutationFn: () => api.put(`/power/calendars/${cal.key}/days/${day}`, { label: label.trim() }),
    onSuccess: async () => {
      setDay("");
      setLabel("");
      await refresh();
    },
  });
  const del = useMutation({ mutationFn: (d: string) => api.delete(`/power/calendars/${cal.key}/days/${d}`), onSuccess: refresh });
  const fmt = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (day && label.trim()) add.mutate();
  };
  return (
    <div className="advanced-body">
      <QueryStatus query={days} />
      {days.isSuccess && days.data.length === 0 ? <p className="meta">{t("power.calendars.no_days")}</p> : null}
      <ul className="list-reset rows">
        {(days.data ?? []).map((d) => (
          <li key={d.day} className="row-line">
            <span>
              {fmt(d.day)} · {d.label}
            </span>
            <button type="button" className="btn btn-text btn-danger" disabled={del.isPending} onClick={() => del.mutate(d.day)} aria-label={t("power.calendars.remove_day", { day: fmt(d.day) })}>
              <Icon name="delete" />
              {t("schedules.rules.remove")}
            </button>
          </li>
        ))}
      </ul>
      {del.isError ? <ErrorAlert error={del.error} /> : null}
      <form className="times" onSubmit={submit} aria-label={t("power.calendars.add_day")}>
        <div className="field">
          <label htmlFor={`${uid}-d`}>{t("power.calendars.day")}</label>
          <input id={`${uid}-d`} type="date" value={day} required onChange={(e) => setDay(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-l`}>{t("power.calendars.day_label")}</label>
          <input id={`${uid}-l`} value={label} maxLength={120} required autoComplete="off" onChange={(e) => setLabel(e.target.value)} />
        </div>
        <button type="submit" className="btn btn-filled" disabled={add.isPending || !day || !label.trim()}>
          <Icon name="add" />
          {t("power.calendars.add_day")}
        </button>
      </form>
      {add.isError ? <ErrorAlert error={add.error} /> : null}
    </div>
  );
}

export function CalendarsSection() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const cals = useCalendars();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [fr, setFr] = useState("");
  const [en, setEn] = useState("");
  const create = useMutation({
    mutationFn: () => api.post("/power/calendars", { key, name: { fr: fr.trim(), en: (en || fr).trim() } }),
    onSuccess: async () => {
      setOpen(false);
      setKey("");
      setFr("");
      setEn("");
      await qc.invalidateQueries({ queryKey: powerKeys.calendars });
    },
  });
  const remove = useMutation({
    mutationFn: (c: Calendar) => api.delete(`/power/calendars/${c.key}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: powerKeys.calendars }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (key && fr.trim()) create.mutate();
  };
  const when = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  return (
    <section className="section" aria-labelledby="calendars-h">
      <div className="page-head">
        <h2 id="calendars-h">
          <Icon name="calendar" />
          {t("power.calendars.title")}
        </h2>
        <button type="button" className="btn btn-filled" disabled={open} onClick={() => setOpen(true)}>
          <Icon name="add" />
          {t("power.calendars.create")}
        </button>
      </div>
      <p className="lead">{t("power.calendars.intro")}</p>
      {open ? (
        <form className="card inline-form" onSubmit={submit} aria-label={t("power.calendars.create")}>
          <h3>{t("power.calendars.create")}</h3>
          <div className="field">
            <label htmlFor={`${uid}-k`}>{t("power.calendars.key")}</label>
            <input id={`${uid}-k`} className="mono" value={key} required maxLength={80} pattern="[a-z0-9][a-z0-9._-]*" autoComplete="off" spellCheck={false} aria-describedby={`${uid}-kh`} onChange={(e) => setKey(e.target.value)} />
            <p className="field-help" id={`${uid}-kh`}>
              {t("power.calendars.key_help")}
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-fr`}>{t("power.calendars.name_fr")}</label>
            <input id={`${uid}-fr`} value={fr} required maxLength={120} autoComplete="off" onChange={(e) => setFr(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-en`}>{t("power.calendars.name_en")}</label>
            <input id={`${uid}-en`} value={en} maxLength={120} autoComplete="off" onChange={(e) => setEn(e.target.value)} />
          </div>
          {create.isError ? <ErrorAlert error={create.error} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={create.isPending}>
              <Icon name="online" />
              {t("common.save")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
      <QueryStatus query={cals} />
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      {cals.isSuccess && cals.data.length === 0 ? <Empty icon="calendar">{t("power.calendars.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {(cals.data ?? []).map((c) => {
          const name = calendarName(c.name, i18n.language, c.key);
          return (
            <li key={c.key} className="card" aria-labelledby={`cal-${c.key}`}>
              <h3 id={`cal-${c.key}`}>
                <Icon name="calendar" />
                {name}
              </h3>
              <p className="meta">
                <code>{c.key}</code> · {t("power.calendars.days_count", { count: c.day_count })}
              </p>
              {c.refreshed_at ? <p className="meta">{t("power.calendars.refreshed", { when: when(c.refreshed_at) })}</p> : null}
              <details className="advanced">
                <summary>{t("power.calendars.days")}</summary>
                <Days cal={c} />
              </details>
              <div className="actions">
                <ConfirmAction label={t("schedules.delete.action")} confirmLabel={t("schedules.delete.confirm", { name })} pending={remove.isPending} onConfirm={() => remove.mutate(c)} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
