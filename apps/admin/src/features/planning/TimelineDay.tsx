import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { cellsOf, NAV_KEYS, nextCell } from "./gridNav";
import { ExpiryMarker } from "./ExpiryMarker";
import { NowCursor } from "./NowCursor";
import { PriorityBadge, SourceOrigin } from "./parts";
import { buildDay, type Block, type Lane, type ScreenTimeline } from "./planning";
import { SegmentBlock } from "./SegmentBlock";
import { timeFormatter } from "./time";

export interface TimelineProps {
  tz: string;
  screen: ScreenTimeline | undefined;
  lanes: Lane[];
  /** Instant inspecté (ms) ou null. */
  atMs: number | null;
  nowMs: number;
  /** Appelé avec l'instant (ms) du bloc choisi (clic ou Entrée). */
  onSelect: (ms: number) => void;
  onNow: () => void;
  /** Clic sur un créneau vide : ouvre l'assistant de programmation pré-rempli (début et fin en ms). */
  onEmpty?: (startMs: number, endMs: number) => void;
}

const TICKS = [0, 3, 6, 9, 12, 15, 18, 21];

/** Grille commune aux vues Jour et Semaine : un roving tabindex pour toute la grille. */
export function TimelineBase({ dates, ...p }: TimelineProps & { dates: string[] }) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const root = useRef<HTMLDivElement>(null);
  const fmt = useMemo(() => timeFormatter(i18n.language, p.tz), [i18n.language, p.tz]);
  const days = useMemo(() => dates.map((d) => buildDay(d, p.tz, p.screen, p.lanes)), [dates, p.tz, p.screen, p.lanes]);
  const rowsPerDay = days[0]?.tracks.length ?? 1;
  const first = days.flatMap((d) => d.tracks).find((tr) => tr.blocks.length > 0)?.blocks[0]?.key;
  const [active, setActive] = useState<string | null>(null);
  const known = days.some((d) => d.tracks.some((tr) => tr.blocks.some((b) => b.key === active)));
  const tabKey = known ? active : first;
  const hour = useMemo(() => new Intl.DateTimeFormat(i18n.language, { hour: "numeric", timeZone: "UTC" }), [i18n.language]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const el = root.current;
    const target = (e.target as HTMLElement).closest<HTMLElement>("[data-cell]");
    if (!el || !target) return;
    if (e.key === "n" || e.key === "N") {
      e.preventDefault();
      p.onNow();
      const here = cellsOf(el).find((c) => Number(c.dataset.row) % rowsPerDay === 0 && Number(c.dataset.start) <= p.nowMs && p.nowMs < Number(c.dataset.end));
      here?.focus();
      return;
    }
    if (!NAV_KEYS.includes(e.key)) return;
    const to = nextCell(cellsOf(el), target, e.key, rowsPerDay);
    e.preventDefault();
    to?.focus();
  };

  const select = (b: Block) => (p.onEmpty && b.segment?.state === "off" ? p.onEmpty(b.startMs, b.endMs) : p.onSelect(b.startMs));

  return (
    <div className="timeline" ref={root}>
      <p className="field-help" id={`${uid}-help`}>
        {t("planning.grid.help")}
      </p>
      {days.map((day, di) => {
        const hid = `${uid}-d${di}`;
        const nowIn = p.nowMs >= day.startMs && p.nowMs < day.endMs;
        const label = fmt.day(day.startMs + 1);
        return (
          <section key={day.date} className="tl-day" aria-labelledby={hid}>
            <h3 id={hid}>{label}</h3>
            <div className="tl-scroll">
              <div className="tl-inner">
                <div className="tl-head">
                  <div className="tl-axis" aria-hidden="true">
                    {TICKS.map((h) => (
                      <span key={h} className="tl-tick" style={{ insetInlineStart: `${(h / 24) * 100}%` }}>
                        {hour.format(Date.UTC(2000, 0, 1, h))}
                      </span>
                    ))}
                  </div>
                  <div className="tl-markers">
                    {day.markers.map((m) => (
                      <ExpiryMarker key={`${m.kind}-${m.ref_id}-${m.at}`} marker={m} left={((Date.parse(m.at) - day.startMs) / (day.endMs - day.startMs)) * 100} time={fmt.time(m.at)} />
                    ))}
                  </div>
                </div>
                <div className="tl-body">
                  {/* Le focus est porté par les blocs (tabindex itinérant), pas par la grille. */}
                  {/* eslint-disable-next-line jsx-a11y/interactive-supports-focus */}
                  <div role="grid" className="tl-grid" onKeyDown={onKeyDown} aria-label={t("planning.grid.aria", { day: label })} aria-describedby={`${uid}-help`} aria-rowcount={day.tracks.length}>
                    {day.tracks.map((track, ti) => {
                      const row = di * rowsPerDay + ti;
                      return (
                        <div role="row" className="tl-row" data-kind={track.kind} key={track.key}>
                          <div role="rowheader" className="tl-label">
                            {track.kind === "effective" ? (
                              <strong>{t("planning.track.effective")}</strong>
                            ) : (
                              <>
                                <strong>{track.lane?.source.name ?? track.lane?.content?.name}</strong>
                                {track.lane?.source.priority_level ? <PriorityBadge level={track.lane.source.priority_level} value={track.lane.source.priority} /> : null}
                                {track.lane ? <SourceOrigin source={track.lane.source} /> : null}
                              </>
                            )}
                          </div>
                          <div className="tl-track">
                            {track.blocks.map((b) => (
                              <SegmentBlock
                                key={b.key}
                                block={b}
                                fmt={fmt}
                                row={row}
                                tabbable={b.key === tabKey}
                                selected={track.kind === "effective" && p.atMs !== null && p.atMs >= b.startMs && p.atMs < b.endMs}
                                onSelect={select}
                                onFocusBlock={setActive}
                                creates={!!p.onEmpty}
                              />
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {nowIn ? <NowCursor ratio={(p.nowMs - day.startMs) / (day.endMs - day.startMs)} time={fmt.time(p.nowMs)} /> : null}
                </div>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

/** Vue Jour : une journée, pistes Effectif puis une par source. */
export function TimelineDay({ date, ...p }: TimelineProps & { date: string }) {
  return <TimelineBase dates={[date]} {...p} />;
}
