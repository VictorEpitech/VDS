import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Empty, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useAllGroups, useScreens, useSites } from "../../lib/queries";
import { useMinuteNow } from "../overrides/overrides";
import { ConflictPanel } from "./ConflictPanel";
import { InstantInspector } from "./InstantInspector";
import { PlanningList } from "./PlanningList";
import { PlanningToolbar } from "./PlanningToolbar";
import { isDate, isLocalInstant, localInstantToUtc, addDays, addMonths, browserTimeZone, dayBounds, firstWeekday, monthGridDays, safeZone, todayIn, utcToLocalInstant, wallTime, wallWeekdayIndex, weekStart } from "./time";
import type { SchedulePrefill } from "../schedule/ScheduleWizard";
import { parseTarget, targetParam, usePlanningTimeline, useNarrow, VIEWS, type PlanningView, type Target } from "./planning";
import { MonthGrid } from "./MonthGrid";
import { ResourceTimeline } from "./ResourceTimeline";
import { TimelineDay } from "./TimelineDay";
import { TimelineWeek } from "./TimelineWeek";

/**
 * Planning : barre d'outils, vue (Jour / Semaine / Liste), inspecteur d'instant et conflits.
 * Toutes les données viennent de `/planning/*` (résolution côté serveur) ; l'état vit dans l'URL, relative à la base du routeur.
 */
export function PlanningPage({ onEdit, onCreate }: { onEdit: (scheduleId: string) => void; onCreate?: (prefill: SchedulePrefill) => void }) {
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
  const monthDays = view === "month" ? monthGridDays(date, firstWeekday(i18n.language)) : null;
  const from = monthDays ? monthDays[0]! : view === "day" ? date : start;
  const to = monthDays ? monthDays[41]! : view === "day" ? date : addDays(start, 6);

  // Groupe ou site en vue Jour : une ligne par écran, 100 écrans par page (curseur du serveur).
  const resources = target !== null && target.type !== "screen" && view === "day";
  const pageKey = `${target ? targetParam(target) : ""}|${from}|${to}|${view}`;
  const [pager, setPager] = useState<{ key: string; stack: (string | null)[] }>({ key: pageKey, stack: [null] });
  const stack = pager.key === pageKey ? pager.stack : [null];
  const cursor = resources ? (stack[stack.length - 1] ?? null) : null;

  const timeline = usePlanningTimeline(target, from, to, { cursor, explain: target?.type === "screen" });
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
  const showNow = () => set({ at: utcToLocalInstant(Date.now(), tz) });
  // Clic sur un créneau vide : l'assistant s'ouvre avec la cible, le jour de la semaine et les heures du créneau (heure du site).
  const onEmpty = onCreate && target
    ? (startMs: number, endMs: number) => {
        const s = wallTime(startMs, tz);
        let e = wallTime(endMs, tz);
        if (e <= s) e = "23:59";
        onCreate({ target, date: utcToLocalInstant(startMs, tz).slice(0, 10), rule: { weekdays: 1 << wallWeekdayIndex(startMs, tz), start: s, end: e } });
      }
    : undefined;
  const openScreen = (id: string) => {
    setPickedScreen(null);
    set({ target: targetParam({ type: "screen", id }), at: null });
  };

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
        onStep={(dir) => set({ date: view === "month" ? addMonths(date, dir) : addDays(date, dir * (view === "day" ? 1 : 7)) })}
        onNow={() => set({ date: todayIn(tz, Date.now()), at: utcToLocalInstant(Date.now(), tz) })}
        tzNote={tzNote}
      />
      {target === null ? <Empty icon="screen">{t("planning.no_target")}</Empty> : <QueryStatus query={timeline} />}
      {data && data.screens.length > 1 && !resources ? (
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
            {resources ? (
              <>
                <ResourceTimeline date={date} tz={tz} screens={data.screens} lanes={lanes} mixed={mixed} atMs={atMs} nowMs={nowMs} onSelect={selectInstant} onNow={showNow} onEmpty={onEmpty} onOpenScreen={openScreen} />
                {stack.length > 1 || data.next_cursor ? (
                  <nav className="pager" aria-label={t("planning.resources.pager")}>
                    <button type="button" className="btn btn-text" disabled={stack.length <= 1} onClick={() => setPager({ key: pageKey, stack: stack.slice(0, -1) })}>
                      <Icon name="back" />
                      {t("planning.resources.prev_page")}
                    </button>
                    <span role="status" className="meta">
                      {t("planning.resources.page", { page: stack.length, count: data.screens.length })}
                    </span>
                    <button type="button" className="btn btn-text" disabled={!data.next_cursor} onClick={() => setPager({ key: pageKey, stack: [...stack, data.next_cursor] })}>
                      {t("planning.resources.next_page")}
                      <Icon name="next" />
                    </button>
                  </nav>
                ) : null}
              </>
            ) : view === "list" ? (
              <PlanningList screen={shown} tz={tz} from={windowLo} to={windowHi} onSelect={selectInstant} onEmpty={onEmpty} />
            ) : view === "month" ? (
              <MonthGrid date={date} tz={tz} screen={shown} nowMs={nowMs} onOpenDay={(d) => set({ view: "day", date: d })} onGoto={(d) => set({ date: d })} />
            ) : view === "day" ? (
              <TimelineDay date={date} tz={tz} screen={shown} lanes={lanes} atMs={atMs} nowMs={nowMs} onSelect={selectInstant} onNow={showNow} onEmpty={onEmpty} />
            ) : (
              <TimelineWeek start={start} tz={tz} screen={shown} lanes={lanes} atMs={atMs} nowMs={nowMs} onSelect={selectInstant} onNow={showNow} onEmpty={onEmpty} />
            )}
            {resources ? null : <ConflictPanel conflicts={shown.conflicts} tz={tz} onEdit={onEdit} />}
          </div>
          {atMs !== null && target ? (
            <InstantInspector target={target} atMs={atMs} tz={tz} onChangeAt={(local) => set({ at: local })} onClose={() => set({ at: null })} onEdit={onEdit} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
