import { useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import type { Group, Playlist, Schedule, Screen, Site } from "../../lib/queries";
import { RulesEditor } from "./RulesEditor";
import { ruleProblem } from "./rules";

export type TargetType = "screen" | "group" | "site";
export interface ScheduleDraft {
  name: string;
  target_type: TargetType;
  target_id: string;
  playlist_id: string;
  priority: number;
  rules: { weekdays: number; start: string; end: string }[];
  starts_on: string | null;
  ends_on: string | null;
  enabled: boolean;
}

interface Props {
  schedule?: Schedule;
  screens: Screen[];
  groups: Group[];
  sites: Site[];
  playlists: Playlist[];
  pending: boolean;
  error: unknown;
  onSubmit: (d: ScheduleDraft) => void;
  onCancel: () => void;
}

/** Nombre d'écrans touchés par une cible (sélecteur de cible avec compteur). */
export function countTargets(type: TargetType, id: string, screens: Screen[]): number {
  const live = screens.filter((s) => !s.archived_at);
  if (type === "screen") return live.some((s) => s.id === id) ? 1 : 0;
  if (type === "group") return live.filter((s) => s.group_id === id).length;
  return live.filter((s) => s.site_id === id).length;
}

export function ScheduleForm({ schedule, screens, groups, sites, playlists, pending, error, onSubmit, onCancel }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [name, setName] = useState(schedule?.name ?? "");
  const [targetType, setTargetType] = useState<TargetType>(schedule?.target_type === "organization" ? "site" : (schedule?.target_type ?? "screen"));
  const [targetId, setTargetId] = useState(schedule?.target_id ?? "");
  const [playlistId, setPlaylistId] = useState(schedule?.playlist_id ?? "");
  const [priority, setPriority] = useState(String(schedule?.priority ?? 0));
  const [rules, setRules] = useState(schedule?.rules ?? []);
  const [startsOn, setStartsOn] = useState(schedule?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(schedule?.ends_on ?? "");
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const options: { id: string; name: string }[] =
    targetType === "screen"
      ? screens.filter((s) => !s.archived_at).map((s) => ({ id: s.id, name: s.name }))
      : targetType === "group"
        ? groups.map((g) => ({ id: g.id, name: g.name }))
        : sites.filter((s) => !s.archived_at).map((s) => ({ id: s.id, name: s.name }));
  const touched = targetId ? countTargets(targetType, targetId, screens) : null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const bad = new Set<string>();
    if (!name.trim()) bad.add("name");
    if (!targetId) bad.add("target");
    if (!playlistId) bad.add("playlist");
    const prio = Number(priority);
    if (!Number.isInteger(prio) || prio < -1000 || prio > 1000) bad.add("priority");
    rules.forEach((r, i) => {
      if (ruleProblem(r)) bad.add(`rule-${i}`);
    });
    setInvalid(bad);
    if (bad.size > 0) {
      if (bad.has("priority")) setAdvancedOpen(true);
      queueMicrotask(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    onSubmit({
      name: name.trim(),
      target_type: targetType,
      target_id: targetId,
      playlist_id: playlistId,
      priority: prio,
      rules,
      starts_on: startsOn || null,
      ends_on: endsOn || null,
      enabled,
    });
  };

  const err = (key: string, text: string, id: string) =>
    invalid.has(key) ? (
      <p className="field-error" id={id}>
        <Icon name="warning" />
        <span>{text}</span>
      </p>
    ) : null;

  return (
    <form ref={formRef} className="card schedule-form" onSubmit={submit} noValidate aria-label={schedule ? t("schedules.edit") : t("schedules.create")}>
      <h2>{schedule ? t("schedules.edit") : t("schedules.create")}</h2>
      <div className="field">
        <label htmlFor={`${uid}-name`}>{t("schedules.name")}</label>
        <input id={`${uid}-name`} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required aria-invalid={invalid.has("name") ? true : undefined} />
        {err("name", t("errors.field_required"), `${uid}-name-e`)}
      </div>

      <fieldset className="field target">
        <legend>{t("schedules.target.label")}</legend>
        <div className="seg" role="radiogroup" aria-label={t("schedules.target.type")}>
          {(["screen", "group", "site"] as const).map((type) => (
            <label key={type} className="seg-item">
              <input
                type="radio"
                name={`${uid}-ttype`}
                value={type}
                checked={targetType === type}
                onChange={() => {
                  setTargetType(type);
                  setTargetId("");
                }}
              />
              <span>{t(`schedules.target.${type}`)}</span>
            </label>
          ))}
        </div>
        <label htmlFor={`${uid}-target`} className="visually-hidden">
          {t(`schedules.target.${targetType}`)}
        </label>
        <select id={`${uid}-target`} value={targetId} onChange={(e) => setTargetId(e.target.value)} required aria-invalid={invalid.has("target") ? true : undefined}>
          <option value="">{t("form.choose")}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        {touched !== null ? (
          <p className="field-help" role="status">
            {t("schedules.target.touches", { count: touched })}
          </p>
        ) : null}
        {err("target", t("errors.field_required"), `${uid}-target-e`)}
      </fieldset>

      <div className="field">
        <label htmlFor={`${uid}-playlist`}>{t("schedules.playlist")}</label>
        <select id={`${uid}-playlist`} value={playlistId} onChange={(e) => setPlaylistId(e.target.value)} required aria-invalid={invalid.has("playlist") ? true : undefined}>
          <option value="">{t("form.choose")}</option>
          {playlists.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {err("playlist", t("errors.field_required"), `${uid}-playlist-e`)}
      </div>

      <RulesEditor
        rules={rules}
        onChange={setRules}
        invalid={new Set([...invalid].filter((k) => k.startsWith("rule-")).map((k) => Number(k.slice(5))))}
        legend={t("schedules.rules.title")}
        emptyHelp={t("schedules.rules.all_day")}
        help={t("schedules.rules.help")}
      />

      <details className="advanced" open={advancedOpen} onToggle={(e) => setAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>{t("form.advanced")}</summary>
        <div className="advanced-body">
          <div className="field">
            <label htmlFor={`${uid}-prio`}>{t("schedules.priority.label")}</label>
            <input id={`${uid}-prio`} type="number" inputMode="numeric" min={-1000} max={1000} step={1} value={priority} onChange={(e) => setPriority(e.target.value)} aria-invalid={invalid.has("priority") ? true : undefined} aria-describedby={`${uid}-prio-h`} />
            <p className="field-help" id={`${uid}-prio-h`}>
              {t("schedules.priority.help")}
            </p>
            {err("priority", t("form.errors.range", { min: -1000, max: 1000 }), `${uid}-prio-e`)}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-from`}>{t("schedules.period.from")}</label>
            <input id={`${uid}-from`} type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-to`}>{t("schedules.period.to")}</label>
            <input id={`${uid}-to`} type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
          </div>
          <div className="field field-check">
            <label>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>{t("schedules.enabled")}</span>
            </label>
          </div>
        </div>
      </details>

      {error ? <ErrorAlert error={error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={pending} aria-busy={pending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-text" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
