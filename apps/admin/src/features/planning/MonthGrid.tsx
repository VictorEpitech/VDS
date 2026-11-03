import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { ScreenTimeline, Segment } from "./planning";
import { addDays, dayBounds, firstWeekday, monthGridDays, timeFormatter } from "./time";

const MAX_SEGMENTS = 3;

interface Props {
  /** Une date du mois affiché. */
  date: string;
  tz: string;
  screen: ScreenTimeline | undefined;
  nowMs: number;
  /** Clic ou Entrée sur un jour : ouvre la vue Jour. */
  onOpenDay: (date: string) => void;
  /** Le clavier sort de la grille (jour hors des 6 semaines) : le mois change autour de cette date. */
  onGoto: (date: string) => void;
}

interface Cell {
  date: string;
  inMonth: boolean;
  startMs: number;
  endMs: number;
  /** Segments effectifs qui jouent ou sont forcés ce jour-là (le vide n'est pas listé). */
  segments: { segment: Segment; from: number; to: number }[];
  conflicts: number;
  expiries: number;
}

/** Regroupement par jour des données du serveur (aucune règle de diffusion). */
function buildCells(date: string, tz: string, first: number, screen: ScreenTimeline | undefined): Cell[] {
  const month = date.slice(0, 7);
  return monthGridDays(date, first).map((d) => {
    const [lo, hi] = dayBounds(d, tz);
    const segments = (screen?.segments ?? [])
      .filter((s) => (s.state === "play" || s.state === "overridden") && Date.parse(s.end) > lo && Date.parse(s.start) < hi)
      .map((s) => ({ segment: s, from: Math.max(Date.parse(s.start), lo), to: Math.min(Date.parse(s.end), hi) }));
    const conflicts = (screen?.conflicts ?? []).filter((c) => Date.parse(c.first_at) >= lo && Date.parse(c.first_at) < hi).length;
    const expiries = (screen?.markers ?? []).filter((m) => (m.kind === "playlist_expires" || m.kind === "media_expires") && Date.parse(m.at) >= lo && Date.parse(m.at) < hi).length;
    return { date: d, inMonth: d.startsWith(month), startMs: lo, endMs: hi, segments, conflicts, expiries };
  });
}

/** Vue Mois : 6 semaines, par jour 3 segments effectifs au plus puis « +N », badges conflits et expirations. */
export function MonthGrid({ date, tz, screen, nowMs, onOpenDay, onGoto }: Props) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const root = useRef<HTMLDivElement>(null);
  const first = firstWeekday(i18n.language);
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const cells = useMemo(() => buildCells(date, tz, first, screen), [date, tz, first, screen]);
  const today = useMemo(() => cells.find((c) => nowMs >= c.startMs && nowMs < c.endMs)?.date, [cells, nowMs]);
  const [active, setActive] = useState<string>(date);
  const focusDate = useRef<string | null>(null);
  const tabbable = cells.some((c) => c.date === active) ? active : (cells.find((c) => c.date === date)?.date ?? cells[0]!.date);
  const weekday = useMemo(() => new Intl.DateTimeFormat(i18n.language, { weekday: "long", timeZone: "UTC" }), [i18n.language]);
  const weekdayShort = useMemo(() => new Intl.DateTimeFormat(i18n.language, { weekday: "short", timeZone: "UTC" }), [i18n.language]);
  const dayNum = useMemo(() => new Intl.DateTimeFormat(i18n.language, { day: "numeric", timeZone: "UTC" }), [i18n.language]);
  const dayLong = useMemo(() => new Intl.DateTimeFormat(i18n.language, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }), [i18n.language]);
  const monthLabel = useMemo(() => new Intl.DateTimeFormat(i18n.language, { month: "long", year: "numeric", timeZone: "UTC" }).format(Date.parse(`${date}T12:00:00Z`)), [i18n.language, date]);
  const utc = (d: string) => Date.parse(`${d}T12:00:00Z`);

  useEffect(() => {
    const want = focusDate.current;
    if (!want) return;
    const el = root.current?.querySelector<HTMLElement>(`[data-date="${want}"]`);
    if (el) {
      focusDate.current = null;
      el.focus();
    }
  });

  const extra = (c: Cell): number => Math.max(0, c.segments.length - MAX_SEGMENTS);
  const name = (s: Segment) => s.content?.name ?? s.source.name ?? "";
  const label = (c: Cell): string => {
    const parts = [dayLong.format(utc(c.date))];
    if (c.date === today) parts.push(t("planning.month.today"));
    parts.push(c.segments.length === 0 ? t("planning.month.nothing") : t("planning.month.segments", { count: c.segments.length }));
    c.segments.slice(0, MAX_SEGMENTS).forEach((x) => parts.push(`${name(x.segment)} ${t("planning.block.range", { range: `${fmt.time(x.from)}–${fmt.time(x.to)}` })}`));
    if (extra(c)) parts.push(t("planning.month.more", { count: extra(c) }));
    if (c.conflicts > 0) parts.push(t("planning.month.conflicts", { count: c.conflicts }));
    if (c.expiries > 0) parts.push(t("planning.month.expiries", { count: c.expiries }));
    return parts.join(". ");
  };

  const move = (from: string, key: string, ctrl: boolean): string | null => {
    switch (key) {
      case "ArrowRight":
        return addDays(from, 1);
      case "ArrowLeft":
        return addDays(from, -1);
      case "ArrowDown":
        return addDays(from, 7);
      case "ArrowUp":
        return addDays(from, -7);
      case "Home": {
        const i = cells.findIndex((c) => c.date === from);
        return ctrl ? cells[0]!.date : (cells[i - (i % 7)]?.date ?? null);
      }
      case "End": {
        const i = cells.findIndex((c) => c.date === from);
        return ctrl ? cells[41]!.date : (cells[i - (i % 7) + 6]?.date ?? null);
      }
      default:
        return null;
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-date]");
    if (!el) return;
    const from = el.dataset.date!;
    let to: string | null;
    if (e.key === "PageDown" || e.key === "PageUp") {
      const [y, m, d] = from.split("-").map(Number) as [number, number, number];
      const n = new Date(Date.UTC(y, m - 1 + (e.key === "PageDown" ? 1 : -1), d));
      to = `${n.getUTCFullYear()}-${String(n.getUTCMonth() + 1).padStart(2, "0")}-${String(n.getUTCDate()).padStart(2, "0")}`;
    } else to = move(from, e.key, e.ctrlKey);
    if (!to) return;
    e.preventDefault();
    focusDate.current = to;
    setActive(to);
    if (!cells.some((c) => c.date === to) || e.key.startsWith("Page")) onGoto(to);
  };

  return (
    <div className="month" ref={root}>
      <p className="field-help" id={`${uid}-help`}>
        {t("planning.month.help")}
      </p>
      <div className="tl-scroll">
        {/* Le focus est porté par les cases (tabindex itinérant), pas par la grille. */}
        {/* eslint-disable-next-line jsx-a11y/interactive-supports-focus */}
        <div role="grid" className="month-grid" aria-label={t("planning.month.aria", { month: monthLabel })} aria-describedby={`${uid}-help`} onKeyDown={onKeyDown}>
          <div role="row" className="month-row month-head">
            {Array.from({ length: 7 }, (_, i) => {
              const dow = (first + i) % 7;
              const ref = Date.UTC(2024, 0, 7 + dow);
              return (
                <div role="columnheader" key={i} aria-label={weekday.format(ref)}>
                  {weekdayShort.format(ref)}
                </div>
              );
            })}
          </div>
          {Array.from({ length: 6 }, (_, w) => (
            <div role="row" className="month-row" key={w}>
              {cells.slice(w * 7, w * 7 + 7).map((c) => (
                <div role="gridcell" key={c.date} className="month-cell" data-out={c.inMonth ? undefined : ""} data-today={c.date === today ? "" : undefined}>
                  <button type="button" className="month-day" data-date={c.date} tabIndex={c.date === tabbable ? 0 : -1} aria-label={label(c)} onClick={() => onOpenDay(c.date)} onFocus={() => setActive(c.date)}>
                    <span className="month-num" aria-hidden="true">
                      {dayNum.format(utc(c.date))}
                    </span>
                    <span className="month-items" aria-hidden="true">
                      {c.segments.slice(0, MAX_SEGMENTS).map((x, i) => (
                        <span key={i} className="month-seg" data-state={x.segment.state}>
                          {x.segment.state === "overridden" ? <Icon name="overridden" /> : null}
                          <span className="tl-name">{name(x.segment)}</span>
                        </span>
                      ))}
                      {extra(c) ? <span className="month-more">{t("planning.month.more", { count: extra(c) })}</span> : null}
                    </span>
                    {c.conflicts > 0 || c.expiries > 0 ? (
                      <span className="month-badges" aria-hidden="true">
                        {c.conflicts > 0 ? (
                          <span className="month-badge" data-kind="conflict">
                            <Icon name="warning" />
                            {c.conflicts}
                          </span>
                        ) : null}
                        {c.expiries > 0 ? (
                          <span className="month-badge" data-kind="expiry">
                            <Icon name="expiry" />
                            {c.expiries}
                          </span>
                        ) : null}
                      </span>
                    ) : null}
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
