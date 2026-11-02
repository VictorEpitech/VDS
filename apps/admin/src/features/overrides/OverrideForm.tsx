import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge } from "../../components/Provenance";
import { useApi } from "../../lib/api";
import { useConfig, useMedia, usePlaylists, type Group, type Screen, type Site } from "../../lib/queries";
import { newRequestId } from "../supervision/commands";
import { useDurationText, useTimeText } from "./OverrideControls";
import { FALLBACK_DEFAULT_MINUTES, FALLBACK_PRESETS, mediaContent, refreshAfterOverride, wholeGroup, type OverrideCreate, type OverridePreview, type OverrideTargetType } from "./overrides";

type NodeType = Exclude<OverrideTargetType, "organization">;
interface Target {
  type: NodeType;
  id: string;
  name: string;
}
export type OverrideInitial = { type: NodeType; id: string } | { selection: readonly Screen[] } | null;

interface Props {
  screens: readonly Screen[];
  groups: readonly Group[];
  sites: readonly Site[];
  initial: OverrideInitial;
  /** Seul l'administrateur peut forcer l'extinction. */
  isAdmin: boolean;
  onClose: () => void;
  onDone: (message: string) => void;
}

const KEY_DEFAULT = "override.default_duration_minutes";
const KEY_PRESETS = "override.duration_presets";

/** `datetime-local` (heure locale du navigateur) → instant ISO. */
const localToIso = (v: string): string => new Date(v).toISOString();
const toLocalInput = (d: Date): string => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

/** Formulaire « Forcer l'affichage » : 3 champs (quoi, où, jusqu'à quand), aperçu avant validation, options sous « Avancé ». */
export function OverrideForm({ screens, groups, sites, initial, isAdmin, onClose, onDone }: Props) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const time = useTimeText();
  const dur = useDurationText();
  const playlists = usePlaylists();
  const media = useMedia();

  const selection = initial && "selection" in initial ? initial.selection : null;
  const liveScreens = screens.filter((s) => !s.archived_at);
  const group = selection ? wholeGroup(selection, screens, groups) : null;
  const [selMode, setSelMode] = useState<"screens" | "group">(group ? "group" : "screens");
  const [targetType, setTargetType] = useState<NodeType>(initial && "type" in initial ? initial.type : "screen");
  const [targetId, setTargetId] = useState(initial && "id" in initial ? initial.id : "");

  const [what, setWhat] = useState("");
  const [search, setSearch] = useState("");
  const [durChoice, setDurChoice] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [priority, setPriority] = useState("0");
  const [respect, setRespect] = useState<"default" | "yes" | "no">("default");
  const [off, setOff] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set());
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const requestIds = useRef(new Map<string, string>());

  useEffect(() => {
    formRef.current?.querySelector<HTMLElement>("select, input")?.focus();
  }, []);

  const options: { id: string; name: string }[] =
    targetType === "screen" ? liveScreens.map((s) => ({ id: s.id, name: s.name })) : targetType === "group" ? groups.map((g) => ({ id: g.id, name: g.name })) : sites.filter((s) => !s.archived_at).map((s) => ({ id: s.id, name: s.name }));
  const targets: Target[] = selection
    ? selMode === "group" && group
      ? [{ type: "group", id: group.id, name: group.name }]
      : selection.map((s) => ({ type: "screen", id: s.id, name: s.name }))
    : targetId
      ? [{ type: targetType, id: targetId, name: options.find((o) => o.id === targetId)?.name ?? "" }]
      : [];
  const single = targets.length === 1 ? targets[0] : undefined;

  // Durée par défaut et durées rapides : configuration héritée, résolue pour la cible (repli si illisible).
  const cfg = useConfig(single?.type ?? "screen", single?.id ?? "", "core", !!single);
  const cfgDefault = cfg.data?.values[KEY_DEFAULT];
  const cfgPresets = cfg.data?.values[KEY_PRESETS];
  const defaultMinutes = typeof cfgDefault?.value === "number" && cfgDefault.value > 0 ? cfgDefault.value : FALLBACK_DEFAULT_MINUTES;
  const presets = useMemo(() => {
    const raw = Array.isArray(cfgPresets?.value) ? (cfgPresets.value as unknown[]).filter((v): v is number | "end_of_day" => v === "end_of_day" || (typeof v === "number" && v > 0)) : [];
    const list = raw.length ? raw : [...FALLBACK_PRESETS];
    return list.includes(defaultMinutes) ? list : [defaultMinutes, ...list];
  }, [cfgPresets, defaultMinutes]);
  const choice = durChoice ?? String(defaultMinutes);

  const playlistList = (playlists.data?.items ?? []).filter((p) => !search.trim() || p.name.toLowerCase().includes(search.trim().toLowerCase()) || what === `p:${p.id}`);
  const mediaList = (media.data?.items ?? []).filter((m) => m.status === "ready" && !m.expired && (!search.trim() || m.title.toLowerCase().includes(search.trim().toLowerCase()) || what === `m:${m.id}`));

  const build = (target: Target, withRequestId: boolean): OverrideCreate | null => {
    const body: OverrideCreate = { target_type: target.type, target_id: target.id, kind: off ? "off" : "playlist", priority: 0 };
    if (!off) {
      if (what.startsWith("p:")) body.playlist_id = what.slice(2);
      else if (what.startsWith("m:")) {
        const m = media.data?.items.find((x) => x.id === what.slice(2));
        if (!m) return null;
        body.content = mediaContent(m);
      } else return null;
    }
    if (choice === "end_of_day") body.until = "end_of_day";
    else if (choice === "date") {
      if (!date || Number.isNaN(new Date(date).getTime())) return null;
      body.ends_at = localToIso(date);
    } else body.duration_minutes = Number(choice);
    if (startsAt) body.starts_at = localToIso(startsAt);
    const prio = Number(priority);
    if (Number.isInteger(prio)) body.priority = Math.max(-1000, Math.min(1000, prio));
    if (respect !== "default") body.respect_scheduled_off = respect === "yes";
    if (name.trim()) body.name = name.trim();
    if (note.trim()) body.note = note.trim();
    if (withRequestId) {
      const rid = requestIds.current.get(target.id) ?? newRequestId();
      requestIds.current.set(target.id, rid);
      body.request_id = rid;
    }
    return body;
  };

  const previews = useQueries({
    queries: targets.map((target) => {
      const body = build(target, false);
      return { queryKey: ["overrides", "preview", body] as const, enabled: body !== null, retry: false, gcTime: 0, queryFn: () => api.post<OverridePreview>("/overrides/preview", body) };
    }),
  });
  const ready = previews.length > 0 && previews.every((p) => p.isSuccess);
  const failed = previews.find((p) => p.isError);
  const sum = ready
    ? previews.reduce(
        (acc, p) => {
          const d = p.data as OverridePreview;
          acc.total += d.screens_total;
          acc.offline += d.screens_offline;
          acc.off += d.screens_in_scheduled_off;
          d.superseded_override_ids.forEach((i) => acc.superseded.add(i));
          return acc;
        },
        { total: 0, offline: 0, off: 0, superseded: new Set<string>() },
      )
    : null;
  const endsAt = ready ? (previews[0]?.data as OverridePreview).ends_at : null;

  const create = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(targets.map((target) => api.post(`/overrides`, build(target, true))));
      const bad = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      if (bad) throw bad.reason;
    },
    onSuccess: async () => {
      await refreshAfterOverride(qc);
      onDone(t("overrides.created", { count: sum?.total ?? targets.length, when: endsAt ? time(endsAt) : "" }));
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const bad = new Set<string>();
    if (!targets.length) bad.add("target");
    if (!off && !what) bad.add("what");
    if (choice === "date" && (!date || new Date(date).getTime() <= Date.now())) bad.add("date");
    const prio = Number(priority);
    if (!Number.isInteger(prio) || prio < -1000 || prio > 1000) bad.add("priority");
    setInvalid(bad);
    if (bad.size > 0) {
      if (bad.has("priority")) setAdvancedOpen(true);
      queueMicrotask(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    create.mutate();
  };

  const err = (key: string, text: string, id: string) =>
    invalid.has(key) ? (
      <p className="field-error" id={id}>
        <Icon name="warning" />
        <span>{text}</span>
      </p>
    ) : null;
  const chipLabel = (v: number | "end_of_day") => (v === "end_of_day" ? t("overrides.duration.end_of_day") : dur(v));

  return (
    <form ref={formRef} className="card override-form" onSubmit={submit} noValidate aria-label={t("overrides.form.title")}>
      <h2>
        <Icon name="overridden" />
        {t("overrides.form.title")}
      </h2>
      <p className="meta">{t("overrides.form.rule")}</p>

      <div className="field">
        <label htmlFor={`${uid}-what`}>{t("overrides.what.label")}</label>
        <input id={`${uid}-search`} type="search" className="override-search" value={search} disabled={off} aria-label={t("overrides.what.search")} placeholder={t("overrides.what.search")} onChange={(e) => setSearch(e.target.value)} />
        <select id={`${uid}-what`} value={what} disabled={off} onChange={(e) => setWhat(e.target.value)} required aria-invalid={invalid.has("what") ? true : undefined} aria-describedby={invalid.has("what") ? `${uid}-what-e` : undefined}>
          <option value="">{t("form.choose")}</option>
          {playlistList.length ? (
            <optgroup label={t("overrides.what.playlists")}>
              {playlistList.map((p) => (
                <option key={p.id} value={`p:${p.id}`}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          ) : null}
          {mediaList.length ? (
            <optgroup label={t("overrides.what.media")}>
              {mediaList.map((m) => (
                <option key={m.id} value={`m:${m.id}`}>
                  {m.title}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
        {off ? <p className="field-help">{t("overrides.what.off_help")}</p> : null}
        {err("what", t("errors.field_required"), `${uid}-what-e`)}
      </div>

      <fieldset className="field target">
        <legend>{t("overrides.where.label")}</legend>
        {selection ? (
          <>
            <p>{selMode === "group" && group ? t("overrides.where.group_whole", { name: group.name, count: selection.length }) : t("overrides.where.selection", { count: selection.length })}</p>
            {group ? (
              <div className="seg" role="radiogroup" aria-label={t("overrides.where.label")}>
                {(["group", "screens"] as const).map((m) => (
                  <label key={m} className="seg-item">
                    <input type="radio" name={`${uid}-sel`} value={m} checked={selMode === m} onChange={() => setSelMode(m)} />
                    <span>{m === "group" ? t("overrides.where.mode_group", { name: group.name }) : t("overrides.where.mode_screens", { count: selection.length })}</span>
                  </label>
                ))}
              </div>
            ) : null}
          </>
        ) : (
          <>
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
            {err("target", t("errors.field_required"), `${uid}-target-e`)}
          </>
        )}
      </fieldset>

      <fieldset className="field">
        <legend>{t("overrides.when.label")}</legend>
        <div className="seg" role="radiogroup" aria-label={t("overrides.when.label")}>
          {presets.map((p) => (
            <label key={String(p)} className="seg-item">
              <input type="radio" name={`${uid}-dur`} value={String(p)} checked={choice === String(p)} onChange={() => setDurChoice(String(p))} />
              <span>{chipLabel(p)}</span>
            </label>
          ))}
          <label className="seg-item">
            <input type="radio" name={`${uid}-dur`} value="date" checked={choice === "date"} onChange={() => setDurChoice("date")} />
            <span>{t("overrides.duration.date")}</span>
          </label>
        </div>
        {choice === "date" ? (
          <div className="field">
            <label htmlFor={`${uid}-date`}>{t("overrides.when.date")}</label>
            <input id={`${uid}-date`} type="datetime-local" min={toLocalInput(new Date())} value={date} onChange={(e) => setDate(e.target.value)} required aria-invalid={invalid.has("date") ? true : undefined} aria-describedby={invalid.has("date") ? `${uid}-date-e` : undefined} />
            {err("date", t("overrides.when.date_invalid"), `${uid}-date-e`)}
          </div>
        ) : null}
        {cfgDefault ? (
          <p className="provenance-row">
            <span className="meta">{t("overrides.when.default", { duration: dur(defaultMinutes) })}</span>
            <ProvenanceBadge origin={cfgDefault.origin} level={single?.type ?? "screen"} />
          </p>
        ) : null}
        {choice === "end_of_day" ? <p className="field-help">{t("overrides.when.end_of_day_help")}</p> : null}
      </fieldset>

      <details className="advanced" open={advancedOpen} onToggle={(e) => setAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>{t("form.advanced")}</summary>
        <div className="advanced-body">
          <div className="field">
            <label htmlFor={`${uid}-start`}>{t("overrides.advanced.starts_at")}</label>
            <input id={`${uid}-start`} type="datetime-local" min={toLocalInput(new Date())} value={startsAt} onChange={(e) => setStartsAt(e.target.value)} aria-describedby={`${uid}-start-h`} />
            <p className="field-help" id={`${uid}-start-h`}>
              {t("overrides.advanced.starts_at_help")}
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-prio`}>{t("overrides.advanced.priority")}</label>
            <input id={`${uid}-prio`} type="number" inputMode="numeric" min={-1000} max={1000} step={1} value={priority} onChange={(e) => setPriority(e.target.value)} aria-invalid={invalid.has("priority") ? true : undefined} aria-describedby={`${uid}-prio-h`} />
            <p className="field-help" id={`${uid}-prio-h`}>
              {t("overrides.advanced.priority_help")}
            </p>
            {err("priority", t("form.errors.range", { min: -1000, max: 1000 }), `${uid}-prio-e`)}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-respect`}>{t("overrides.advanced.respect")}</label>
            <select id={`${uid}-respect`} value={respect} onChange={(e) => setRespect(e.target.value as typeof respect)} aria-describedby={`${uid}-respect-h`}>
              <option value="default">{t("overrides.advanced.respect_default")}</option>
              <option value="yes">{t("overrides.advanced.respect_yes")}</option>
              <option value="no">{t("overrides.advanced.respect_no")}</option>
            </select>
            <p className="field-help" id={`${uid}-respect-h`}>
              {t("overrides.advanced.respect_help")}
            </p>
          </div>
          {isAdmin ? (
            <div className="field field-check">
              <label>
                <input type="checkbox" checked={off} onChange={(e) => setOff(e.target.checked)} />
                <span>{t("overrides.advanced.off")}</span>
              </label>
              <p className="field-help">{t("overrides.advanced.off_help")}</p>
            </div>
          ) : null}
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("overrides.advanced.name")}</label>
            <input id={`${uid}-name`} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-note`}>{t("overrides.advanced.note")}</label>
            <textarea id={`${uid}-note`} value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
      </details>

      <div className="override-preview" role="status" aria-live="polite" aria-label={t("overrides.preview.title")}>
        {previews.some((p) => p.isFetching) && !ready ? (
          <p className="meta">
            <Icon name="processing" /> {t("overrides.preview.loading")}
          </p>
        ) : null}
        {sum && endsAt ? (
          <>
            <p>
              <Icon name="check" /> {t("overrides.preview.summary", { count: sum.total, when: time(endsAt) })}
            </p>
            {sum.offline > 0 ? (
              <p className="alert alert-warning">
                <Icon name="warning" />
                <span>{t("overrides.preview.offline", { count: sum.offline, when: time(endsAt) })}</span>
              </p>
            ) : null}
            {sum.off > 0 ? (
              <p className="meta">
                <Icon name="scheduled_off" /> {respect === "yes" ? t("overrides.preview.scheduled_off_respected", { count: sum.off }) : t("overrides.preview.scheduled_off", { count: sum.off })}
              </p>
            ) : null}
            {sum.superseded.size > 0 ? (
              <p className="meta">
                <Icon name="overridden" /> {t("overrides.preview.superseded", { count: sum.superseded.size })}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
      {failed ? <ErrorAlert error={failed.error} /> : null}
      {create.isError ? <ErrorAlert error={create.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={create.isPending || !!failed} aria-busy={create.isPending}>
          <Icon name="overridden" />
          {t("overrides.form.submit")}
        </button>
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
