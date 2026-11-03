import { useQuery } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import type { TFunction } from "i18next";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import type { ChangeOut, Conflict, TargetType } from "./planning";
import { addDays, browserTimeZone, safeZone, timeFormatter, todayIn, type Fmt } from "./time";

type S = components["schemas"];
export type SimulateIn = S["SimulateIn"];
export type SimulateOut = S["SimulateOut"];

/** Délai avant de relancer la simulation pendant la saisie. */
export const SIMULATE_DEBOUNCE_MS = 400;
/** Phrases affichées avant « …et N autres modifications ». */
const MAX_PHRASES = 6;
/** Fenêtre simulée (jours), sous la borne de 14 jours du serveur. */
const WINDOW_DAYS = 7;

export interface SimulateDraft {
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

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

/** Corps de `POST /planning/simulate` : une nouvelle programmation (`draft`) ou la modification d'une existante (`edit`). */
export function simulateBody(draft: SimulateDraft, scheduleId: string | undefined, nowMs: number): SimulateIn {
  const base = todayIn(browserTimeZone(), nowMs);
  const from = draft.starts_on && draft.starts_on > base ? draft.starts_on : base;
  const common = { target_type: draft.target_type, target_id: draft.target_id, from, to: addDays(from, WINDOW_DAYS - 1) };
  return scheduleId ? { ...common, edit: [scheduleId, draft] } : { ...common, draft };
}

type Kind = "replace" | "add" | "remove" | "other";

/** Lecture d'un changement serveur (avant/après) en une catégorie de phrase ; aucune résolution. */
function kindOf(c: ChangeOut): Kind {
  const before = c.before.content;
  const after = c.after.content;
  if (after && before && after.id !== before.id) return "replace";
  if (after && !before) return "add";
  if (!after && before) return "remove";
  if (after && before) return "other";
  return "other";
}

/**
 * Phrases de l'assistant à partir de `changes` : « Remplace « Accueil » par « Promo » sur Hall, mardi 3 novembre, de 08:00 à 12:00. »
 * Les changements identiques sur plusieurs écrans sont regroupés (« sur 12 écrans »).
 */
export function describeChanges(changes: ChangeOut[], names: Map<string, string>, tzOf: (screenId: string) => string, lang: string, t: TFunction): string[] {
  const groups = new Map<string, { kind: Kind; before: string; after: string; start: string; end: string; screens: string[]; tz: string }>();
  for (const c of changes) {
    const kind = kindOf(c);
    const key = `${kind}|${c.before.content?.id ?? ""}|${c.after.content?.id ?? ""}|${c.start}|${c.end}`;
    const g = groups.get(key);
    if (g) g.screens.push(c.screen_id);
    else groups.set(key, { kind, before: c.before.content?.name ?? "", after: c.after.content?.name ?? "", start: c.start, end: c.end, screens: [c.screen_id], tz: tzOf(c.screen_id) });
  }
  const fmts = new Map<string, Fmt>();
  const fmtFor = (tz: string) => {
    let f = fmts.get(tz);
    if (!f) fmts.set(tz, (f = timeFormatter(lang, safeZone(tz))));
    return f;
  };
  return [...groups.values()].map((g) => {
    const f = fmtFor(g.tz);
    // La fin est exclusive : un créneau qui se termine à minuit reste sur le même jour.
    const sameDay = f.day(g.start) === f.day(Date.parse(g.end) - 1);
    const when = sameDay ? t("schedules.wizard.when_same", { day: f.day(g.start), from: f.time(g.start), to: f.time(g.end) }) : t("schedules.wizard.when_span", { from: f.full(g.start), to: f.full(g.end) });
    const where = g.screens.length === 1 ? t("schedules.wizard.on_screen", { name: names.get(g.screens[0]!) ?? "" }) : t("schedules.wizard.on_screens", { count: g.screens.length });
    return t("schedules.wizard.change", { kind: g.kind, before: g.before, after: g.after, where, when });
  });
}

function ConflictLine({ c, fmt }: { c: Conflict; fmt: Fmt }) {
  const { t } = useTranslation();
  const name = c.source.name ?? "";
  const other = c.other?.name ?? "";
  return (
    <li className="conflict" data-severity={c.severity}>
      <span className="conflict-sev">
        <Icon name={c.severity === "warning" ? "warning" : "detect"} />
        <span>{t(`planning.conflict.severity.${c.severity}`)}</span>
      </span>
      <p>{t(`planning.conflict.${c.kind}`, { name, other, hasOther: other ? "yes" : "no", count: c.occurrences, when: fmt.full(c.first_at) })}</p>
    </li>
  );
}

/**
 * Étape « Aperçu » : simule la programmation sur le serveur (`POST /planning/simulate`, sans écriture), anti-rebond de 400 ms,
 * résultat annoncé dans une région `aria-live="polite"`. Aucune résolution côté admin.
 */
export function SimulationPreview({ draft, scheduleId, nowMs }: { draft: SimulateDraft | null; scheduleId?: string; nowMs: number }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const raw = useMemo(() => (draft ? JSON.stringify(simulateBody(draft, scheduleId, nowMs)) : null), [draft, scheduleId, nowMs]);
  const settled = useDebounced(raw, SIMULATE_DEBOUNCE_MS);
  const query = useQuery({
    queryKey: ["planning", "simulate", settled],
    enabled: settled !== null && settled === raw,
    retry: false,
    gcTime: 30_000,
    staleTime: 30_000,
    queryFn: () => api.post<SimulateOut>("/planning/simulate", JSON.parse(settled!)),
  });
  const out = settled === raw ? query.data : undefined;
  const after = out?.after;
  const tz = safeZone(after?.timezone);
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const names = useMemo(() => new Map((after?.screens ?? []).map((s) => [s.screen_id, s.screen_name])), [after]);
  const zones = useMemo(() => new Map((after?.screens ?? []).map((s) => [s.screen_id, s.timezone])), [after]);
  const phrases = useMemo(() => (out ? describeChanges(out.changes, names, (id) => zones.get(id) ?? tz, i18n.language, t) : []), [out, names, zones, tz, i18n.language, t]);
  const busy = raw !== null && (settled !== raw || query.isFetching);

  let body: React.ReactNode;
  if (raw === null) {
    body = (
      <p className="meta">
        <Icon name="detect" /> {t("schedules.wizard.preview.incomplete")}
      </p>
    );
  } else if (busy) {
    body = (
      <p className="meta">
        <Icon name="processing" /> {t("schedules.wizard.preview.loading")}
      </p>
    );
  } else if (query.isError) {
    body = <ErrorAlert error={query.error} />;
  } else if (out) {
    const shown = phrases.slice(0, MAX_PHRASES);
    const hidden = phrases.length - shown.length;
    body = (
      <>
        <p className="preview-summary">
          <Icon name="check" /> <strong>{t("schedules.wizard.preview.touches", { count: out.screens_touched })}</strong>
        </p>
        {phrases.length === 0 ? (
          <p className="meta">{t("schedules.wizard.preview.no_change")}</p>
        ) : (
          <ul className="list-reset preview-changes">
            {shown.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
            {hidden ? <li className="meta">{t("schedules.wizard.preview.more", { count: hidden })}</li> : null}
          </ul>
        )}
        {out.new_conflicts.length > 0 ? (
          <>
            <h4>{t("schedules.wizard.preview.new_conflicts", { count: out.new_conflicts.length })}</h4>
            <ul className="list-reset conflict-list">
              {out.new_conflicts.map((c, i) => (
                <ConflictLine key={`${c.kind}-${i}`} c={c} fmt={fmt} />
              ))}
            </ul>
          </>
        ) : null}
        {out.resolved_conflicts.length > 0 ? (
          <p className="meta">
            <Icon name="check" /> {t("schedules.wizard.preview.resolved", { count: out.resolved_conflicts.length })}
          </p>
        ) : null}
      </>
    );
  } else body = null;

  return (
    <section className="wizard-preview" aria-labelledby="wizard-preview-h">
      <h3 id="wizard-preview-h">{t("schedules.wizard.step.preview")}</h3>
      <div role="status" aria-live="polite" aria-atomic="false" aria-busy={busy}>
        {body}
      </div>
    </section>
  );
}
