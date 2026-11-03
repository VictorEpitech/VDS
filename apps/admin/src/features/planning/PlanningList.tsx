import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Empty } from "../../components/feedback";
import { StatusBadge } from "../../components/StatusBadge";
import { PriorityBadge, SourceOrigin } from "./parts";
import type { ScreenTimeline } from "./planning";
import { timeFormatter } from "./time";

/** Vue Liste : les mêmes segments que la grille, en tableau (alternative textuelle, vue par défaut sous 600 px). */
export function PlanningList({ screen, tz, from, to, onSelect }: { screen: ScreenTimeline | undefined; tz: string; from: number; to: number; onSelect: (ms: number) => void }) {
  const { t, i18n } = useTranslation();
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const rows = (screen?.segments ?? []).filter((s) => Date.parse(s.end) > from && Date.parse(s.start) < to);
  if (rows.length === 0) return <Empty icon="schedule">{t("planning.list.empty")}</Empty>;
  const head = (k: string) => t(`planning.list.${k}`);
  return (
    <div className="table-wrap">
      <table className="planning-table">
        <caption>{t("planning.list.caption", { name: screen?.screen_name ?? "" })}</caption>
        <thead>
          <tr>
            <th scope="col">{head("start")}</th>
            <th scope="col">{head("end")}</th>
            <th scope="col">{head("content")}</th>
            <th scope="col">{head("source")}</th>
            <th scope="col">{head("level")}</th>
            <th scope="col">{head("state")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => {
            const name = s.state === "off" ? t("planning.segment_state.off") : s.state === "scheduled_off" ? t("states.scheduled_off") : (s.content?.name ?? s.source.name ?? "");
            const startMs = Date.parse(s.start);
            return (
              <tr key={`${s.start}-${i}`} data-state={s.state}>
                <th scope="row" data-label={head("start")} className="tabular">
                  <button type="button" className="btn btn-text link-like" onClick={() => onSelect(startMs)} aria-label={t("planning.list.inspect", { when: fmt.full(startMs), name })}>
                    {fmt.dayShort(startMs)} {fmt.time(startMs)}
                  </button>
                </th>
                <td data-label={head("end")} className="tabular">
                  {fmt.dayShort(s.end)} {fmt.time(s.end)}
                </td>
                <td data-label={head("content")}>{name}</td>
                <td data-label={head("source")}>{s.source.kind === "none" ? t("planning.source.none") : <><span>{s.source.name}</span> <SourceOrigin source={s.source} /></>}</td>
                <td data-label={head("level")}>{s.source.priority_level && s.state !== "off" && s.state !== "scheduled_off" ? <PriorityBadge level={s.source.priority_level} value={s.source.priority} /> : "–"}</td>
                <td data-label={head("state")}>
                  {s.state === "overridden" || s.state === "scheduled_off" ? <StatusBadge state={s.state} /> : <span>{t(`planning.segment_state.${s.state}`)}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
