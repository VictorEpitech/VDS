import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { ActiveOverrides } from "../features/overrides/ActiveOverrides";
import { PlanningPage } from "../features/planning/PlanningPage";
import { planningKeys } from "../features/planning/planning";
import { PriorityBadge } from "../features/planning/parts";
import { dayRanges } from "../features/schedule/rules";
import { ScheduleForm, type ScheduleDraft } from "../features/schedule/ScheduleForm";
import { useApi } from "../lib/api";
import { keys, useAllGroups, usePlaylists, useSchedules, useScreens, useSites, type Schedule } from "../lib/queries";

export function Schedules() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const schedules = useSchedules();
  const screens = useScreens();
  const sites = useSites();
  const groups = useAllGroups();
  const playlists = usePlaylists();
  const [form, setForm] = useState<"new" | Schedule | null>(null);

  const done = async () => {
    setForm(null);
    await Promise.all([qc.invalidateQueries({ queryKey: keys.schedules }), qc.invalidateQueries({ queryKey: planningKeys.all })]);
  };
  const save = useMutation({
    mutationFn: (d: ScheduleDraft) => (form && form !== "new" ? api.patch(`/schedules/${form.id}`, d) : api.post("/schedules", d)),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: (s: Schedule) => api.delete(`/schedules/${s.id}`),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: keys.schedules }), qc.invalidateQueries({ queryKey: planningKeys.all })]),
  });

  const targetName = (s: Schedule): string => {
    const list: { id: string; name: string }[] =
      s.target_type === "screen" ? (screens.data ?? []) : s.target_type === "group" ? (groups.data ?? []) : (sites.data ?? []);
    return list.find((x) => x.id === s.target_id)?.name ?? "";
  };
  const day = (i: number) => new Intl.DateTimeFormat(i18n.language, { weekday: "short", timeZone: "UTC" }).format(Date.UTC(2024, 0, 1 + i));
  const rulesText = (s: Schedule): string =>
    s.rules.length === 0
      ? t("schedules.rules.all_day_short")
      : s.rules
          .map((r) => {
            const days = dayRanges(r.weekdays)
              .map(([a, b]) => (a === b ? day(a) : `${day(a)}-${day(b)}`))
              .join(", ");
            return `${days} ${r.start}-${r.end}`;
          })
          .join(" / ");

  return (
    <>
      <div className="page-head">
        <h1>{t("schedules.title")}</h1>
        {form === null ? (
          <button type="button" className="btn btn-filled" onClick={() => { save.reset(); setForm("new"); }}>
            <Icon name="add" />
            {t("schedules.create")}
          </button>
        ) : null}
      </div>
      <p className="lead">{t("schedules.intro")}</p>
      <ActiveOverrides />
      <PlanningPage
        onEdit={(id) => {
          const found = schedules.data?.find((x) => x.id === id);
          if (found) {
            save.reset();
            setForm(found);
          }
        }}
      />
      {form !== null ? (
        <ScheduleForm
          key={form === "new" ? "new" : form.id}
          schedule={form === "new" ? undefined : form}
          screens={screens.data ?? []}
          groups={groups.data ?? []}
          sites={sites.data ?? []}
          playlists={playlists.data?.items ?? []}
          pending={save.isPending}
          error={save.error}
          onSubmit={(d) => save.mutate(d)}
          onCancel={() => setForm(null)}
        />
      ) : null}
      <h2>{t("schedules.all")}</h2>
      <QueryStatus query={schedules} />
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      {schedules.isSuccess && schedules.data.length === 0 ? (
        <Empty
          icon="schedule"
          action={
            form === null ? (
              <button type="button" className="btn btn-filled" onClick={() => { save.reset(); setForm("new"); }}>
                <Icon name="add" />
                {t("schedules.create")}
              </button>
            ) : undefined
          }
        >
          {t("schedules.empty")}
        </Empty>
      ) : null}
      <ul className="rows list-reset">
        {schedules.data?.map((s) => (
          <li key={s.id} className="row card" aria-labelledby={`sch-${s.id}`}>
            <div className="row-main">
              <h2 id={`sch-${s.id}`}>{s.name}</h2>
              <p className="meta">
                {t(`schedules.target.${s.target_type}`)} : {targetName(s)} · {playlists.data?.items.find((p) => p.id === s.playlist_id)?.name ?? ""}
              </p>
              <p className="meta">
                {rulesText(s)} · <PriorityBadge level={s.priority_level} value={s.priority} />
                {s.enabled ? "" : ` · ${t("schedules.disabled")}`}
              </p>
            </div>
            <div className="actions">
              <button type="button" className="btn btn-text" onClick={() => { save.reset(); setForm(s); }}>
                {t("schedules.edit")}
              </button>
              <ConfirmAction
                label={t("schedules.delete.action")}
                confirmLabel={t("schedules.delete.confirm", { name: s.name })}
                pending={remove.isPending}
                onConfirm={() => remove.mutate(s)}
              />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
