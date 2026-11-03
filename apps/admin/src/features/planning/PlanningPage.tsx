import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Empty, QueryStatus } from "../../components/feedback";
import { useAllGroups, useScreens, useSites } from "../../lib/queries";
import { useMinuteNow } from "../overrides/overrides";
import { ConflictPanel } from "./ConflictPanel";
import { InstantInspector } from "./InstantInspector";
import { PlanningList } from "./PlanningList";
import { PlanningToolbar } from "./PlanningToolbar";
import { isDate, isLocalInstant, localInstantToUtc, addDays, browserTimeZone, dayBounds, firstWeekday, safeZone, todayIn, utcToLocalInstant, weekStart } from "./time";
import { parseTarget, targetParam, usePlanningTimeline, useNarrow, VIEWS, type PlanningView, type Target } from "./planning";
import { TimelineDay } from "./TimelineDay";
import { TimelineWeek } from "./TimelineWeek";

/**
 * Planning : barre d'outils, vue (Jour / Semaine / Liste), inspecteur d'instant et conflits.
 * Toutes les données viennent de `/planning/*` (résolution côté serveur) ; l'état vit dans l'URL, relative à la base du routeur.
 */
export function PlanningPage({ onEdit }: { onEdit: (scheduleId: string) => void }) {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const screens = useScreens();
  const sites = useSites();
  const groups = useAllGroups();
  const narrow = useNarrow();
  const nowMs = useMinuteNow().getTime();
  const [pickedScreen, setPickedScreen] = useState<string | null>(null);

  const firstScreen = screens.data?.find((s) => !s.archived_at);
  const target: Target | null = parseTarget(params.get("target")) ?? (firstScreen ? { type: "screen", id: firstScreen.id } : null);
  const rawView = params.get("view");
  const view: PlanningView = VIEWS.find((v) => v === rawView) ?? (narrow ? "list" : "week");
  const rawDate = params.get("date");
  const rawAt = params.get("at");

  // Le fuseau du site vient du serveur ; avant sa réponse, celui du navigateur.
  const [tzKnown, setTzKnown] = useState<string | null>(null);
  const tz = safeZone(tzKnown);
  const date = isDate(rawDate) ? rawDate : todayIn(tz, nowMs);
  const start = weekStart(date, firstWeekday(i18n.language));
  const from = view === "day" ? date : start;
  const to = view === "day" ? date : addDays(start, 6);

  const timeline = usePlanningTimeline(target, from, to);
  const serverTz = timeline.data?.timezone;
  useEffect(() => {
    if (serverTz) setTzKnown(serverTz);
  }, [serverTz]);

  const set = useCallback(
    (patch: Record<string, string | null>) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null) next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: false },
      ),
    [setParams],
  );

  const data = timeline.data;
  const shown = data?.screens.find((s) => s.screen_id === pickedScreen) ?? data?.screens[0];
  const atMs = isLocalInstant(rawAt) ? localInstantToUtc(rawAt, tz) : null;
  const mixed = data?.mixed_timezones ?? false;
  const tzNote = data && (mixed || tz !== browserTimeZone()) ? { tz, mixed } : null;
  const lanes = useMemo(() => data?.lanes ?? [], [data]);
  const [windowLo] = dayBounds(from, tz);
  const [, windowHi] = dayBounds(to, tz);
  const selectInstant = (ms: number) => set({ at: utcToLocalInstant(ms, tz) });

  return (
    <section className="planning" aria-labelledby="planning-h">
      <h2 id="planning-h">{t("planning.title")}</h2>
      <p className="lead">{t("planning.intro")}</p>
      <PlanningToolbar
        target={target}
        onTarget={(next) => {
          setPickedScreen(null);
          set({ target: next ? targetParam(next) : null, at: null });
        }}
        screens={screens.data ?? []}
        groups={groups.data ?? []}
        sites={sites.data ?? []}
        view={view}
        onView={(v) => set({ view: v })}
        date={date}
        onDate={(d) => set({ date: d })}
        onStep={(dir) => set({ date: addDays(date, dir * (view === "day" ? 1 : 7)) })}
        onNow={() => set({ date: todayIn(tz, Date.now()), at: utcToLocalInstant(Date.now(), tz) })}
        tzNote={tzNote}
      />
      {target === null ? <Empty icon="screen">{t("planning.no_target")}</Empty> : <QueryStatus query={timeline} />}
      {data && data.screens.length > 1 ? (
        <div className="field">
          <label htmlFor="planning-screen">{t("planning.screen_picker")}</label>
          <select id="planning-screen" value={shown?.screen_id ?? ""} onChange={(e) => setPickedScreen(e.target.value)}>
            {data.screens.map((s) => (
              <option key={s.screen_id} value={s.screen_id}>
                {s.screen_name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {data && data.screens.length === 0 ? <Empty icon="screen">{t("planning.no_screen")}</Empty> : null}
      {data && shown ? (
        <div className="planning-layout" data-inspecting={atMs !== null ? "" : undefined}>
          <div className="planning-main" aria-busy={timeline.isFetching}>
            {view === "list" ? (
              <PlanningList screen={shown} tz={tz} from={windowLo} to={windowHi} onSelect={selectInstant} />
            ) : view === "day" ? (
              <TimelineDay date={date} tz={tz} screen={shown} lanes={lanes} atMs={atMs} nowMs={nowMs} onSelect={selectInstant} onNow={() => set({ at: utcToLocalInstant(Date.now(), tz) })} />
            ) : (
              <TimelineWeek start={start} tz={tz} screen={shown} lanes={lanes} atMs={atMs} nowMs={nowMs} onSelect={selectInstant} onNow={() => set({ at: utcToLocalInstant(Date.now(), tz) })} />
            )}
            <ConflictPanel conflicts={shown.conflicts} tz={tz} onEdit={onEdit} />
          </div>
          {atMs !== null && target ? (
            <InstantInspector target={target} atMs={atMs} tz={tz} onChangeAt={(local) => set({ at: local })} onClose={() => set({ at: null })} onEdit={onEdit} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
