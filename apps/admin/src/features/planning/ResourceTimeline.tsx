import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ExpiryMarker } from "./ExpiryMarker";
import { NowCursor } from "./NowCursor";
import { PriorityBadge, SourceOrigin } from "./parts";
import { buildDay, type Block, type Lane, type ScreenTimeline } from "./planning";
import { SegmentBlock } from "./SegmentBlock";
import { timeFormatter } from "./time";

/** Hauteur d'une ligne (px) : cible tactile de 44 px + marge ; le fenêtrage en dépend. */
export const ROW_PX = 56;
const OVERSCAN = 4;
/** Hauteur supposée quand le navigateur ne mesure rien (tests, rendu hors écran). */
const FALLBACK_ROWS = 12;
const PAGE_ROWS = 8;
const TICKS = [0, 3, 6, 9, 12, 15, 18, 21];

interface Props {
  date: string;
  /** Fuseau de la cible : l'axe est celui de cette journée murale. */
  tz: string;
  screens: ScreenTimeline[];
  /** Candidats de la cible (programmations et forçages applicables, hérités compris), affichés au-dessus des écrans. */
  lanes: Lane[];
  mixed: boolean;
  atMs: number | null;
  nowMs: number;
  onSelect: (ms: number) => void;
  onNow: () => void;
  onEmpty?: (startMs: number, endMs: number) => void;
  /** Ouvre la vue détaillée d'un écran. */
  onOpenScreen: (screenId: string) => void;
}

type RowModel = { key: string; kind: "lane"; lane: Lane; blocks: Block[] } | { key: string; kind: "screen"; screen: ScreenTimeline; blocks: Block[] };

/** Position clavier : `col` -1 = bouton de la ligne (nom de l'écran), sinon index du bloc dans la ligne. */
interface Pos {
  row: number;
  col: number;
}

/**
 * Groupe/site : une ligne par écran (segments effectifs d'une journée), lignes fenêtrées (seules celles visibles sont rendues),
 * roving tabindex géré sur les données (la ligne active est toujours rendue). Aucune résolution : le serveur fournit les segments.
 */
export function ResourceTimeline({ date, tz, screens, lanes, mixed, atMs, nowMs, onSelect, onNow, onEmpty, onOpenScreen }: Props) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const viewport = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(0);
  const [active, setActive] = useState<Pos>({ row: 0, col: 0 });
  const pendingFocus = useRef<Pos | null>(null);
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const hour = useMemo(() => new Intl.DateTimeFormat(i18n.language, { hour: "numeric", timeZone: "UTC" }), [i18n.language]);

  // Les blocs d'une ligne sont calculés à la demande (seulement pour les lignes rendues ou visées par le clavier).
  const day = useMemo(() => buildDay(date, tz, undefined, lanes), [date, tz, lanes]);
  const count = lanes.length + screens.length;
  // Repères d'expiration de la journée, dédoublonnés sur l'ensemble des écrans de la page.
  const markers = useMemo(() => {
    const seen = new Map<string, (typeof day.markers)[number]>();
    for (const sc of screens)
      for (const m of sc.markers) {
        const at = Date.parse(m.at);
        if ((m.kind === "playlist_expires" || m.kind === "media_expires") && at >= day.startMs && at < day.endMs) seen.set(`${m.kind}-${m.ref_id}-${m.at}`, m);
      }
    return [...seen.values()];
  }, [screens, day]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- le cache est invalidé quand les données ou la journée changent
  const cache = useMemo(() => new Map<number, RowModel>(), [date, tz, screens, lanes]);
  const rowAt = useCallback(
    (i: number): RowModel | null => {
      if (i < 0 || i >= count) return null;
      const hit = cache.get(i);
      if (hit) return hit;
      let row: RowModel;
      if (i < lanes.length) {
        const track = day.tracks[i + 1]!;
        row = { key: `lane-${i}`, kind: "lane", lane: lanes[i]!, blocks: track.blocks.map((b, c) => ({ ...b, key: `r${i}:c${c}` })) };
      } else {
        const screen = screens[i - lanes.length]!;
        const built = buildDay(date, tz, screen, []).tracks[0]!.blocks;
        row = { key: screen.screen_id, kind: "screen", screen, blocks: built.map((b, c) => ({ ...b, key: `r${i}:c${c}` })) };
      }
      cache.set(i, row);
      return row;
    },
    [cache, count, lanes, screens, day, date, tz],
  );

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const measure = () => setViewH(el.clientHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const visibleRows = viewH > 0 ? Math.ceil(viewH / ROW_PX) : FALLBACK_ROWS;
  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const last = Math.min(count - 1, Math.floor(scrollTop / ROW_PX) + visibleRows + OVERSCAN);
  const activeRow = Math.min(active.row, Math.max(count - 1, 0));
  const indices = useMemo(() => {
    const list: number[] = [];
    for (let i = first; i <= last; i++) list.push(i);
    if (count > 0 && (activeRow < first || activeRow > last)) list.push(activeRow);
    return list.sort((a, b) => a - b);
  }, [first, last, activeRow, count]);

  // Tant que la position active n'a pas de bloc (ligne vide), le premier bloc de la première ligne qui en a reste atteignable.
  const activeModel = rowAt(activeRow);
  const tabbable: Pos = useMemo(() => {
    if (activeModel && (active.col === -1 || activeModel.blocks[active.col])) return { row: activeRow, col: active.col };
    for (let i = 0; i < count; i++) if ((rowAt(i)?.blocks.length ?? 0) > 0) return { row: i, col: 0 };
    return { row: activeRow, col: -1 };
  }, [activeModel, active.col, activeRow, count, rowAt]);

  useEffect(() => {
    const want = pendingFocus.current;
    if (!want) return;
    pendingFocus.current = null;
    const sel = want.col === -1 ? `[data-rt="${want.row}:-1"]` : `[data-key="r${want.row}:c${want.col}"]`;
    const el = viewport.current?.querySelector<HTMLElement>(sel);
    el?.focus();
  });

  const go = (to: Pos) => {
    const el = viewport.current;
    if (el) {
      const top = to.row * ROW_PX;
      const h = el.clientHeight || FALLBACK_ROWS * ROW_PX;
      if (top < el.scrollTop) el.scrollTop = top;
      else if (top + ROW_PX > el.scrollTop + h) el.scrollTop = top + ROW_PX - h;
      setScrollTop(el.scrollTop);
    }
    pendingFocus.current = to;
    setActive(to);
  };

  const facing = (row: RowModel | null, from: Block | undefined): number => {
    if (!row || row.blocks.length === 0) return -1;
    if (!from) return 0;
    let c = 0;
    row.blocks.forEach((b, i) => {
      if (b.startMs <= from.startMs) c = i;
    });
    return c;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const holder = (e.target as HTMLElement).closest<HTMLElement>("[data-rt],[data-key]");
    if (!holder) return;
    if (e.key === "n" || e.key === "N") {
      e.preventDefault();
      onNow();
      return;
    }
    const m = holder.dataset.key ? /^r(\d+):c(\d+)$/.exec(holder.dataset.key) : null;
    const [r, c] = (m ? [Number(m[1]), Number(m[2])] : holder.dataset.rt!.split(":").map(Number)) as [number, number];
    const row = rowAt(r);
    if (!row) return;
    const verticalTo = (target: number): Pos | null => {
      const bounded = Math.min(Math.max(target, 0), count - 1);
      if (bounded === r) return null;
      const model = rowAt(bounded);
      const col = c === -1 ? -1 : facing(model, row.blocks[c]);
      return { row: bounded, col };
    };
    let next: Pos | null = null;
    switch (e.key) {
      case "ArrowRight":
        next = c + 1 < row.blocks.length ? { row: r, col: c + 1 } : null;
        break;
      case "ArrowLeft":
        next = c - 1 >= (row.kind === "screen" ? -1 : 0) ? { row: r, col: c - 1 } : null;
        break;
      case "ArrowDown":
        next = verticalTo(r + 1);
        break;
      case "ArrowUp":
        next = verticalTo(r - 1);
        break;
      case "PageDown":
        next = verticalTo(r + PAGE_ROWS);
        break;
      case "PageUp":
        next = verticalTo(r - PAGE_ROWS);
        break;
      case "Home":
        next = e.ctrlKey ? verticalTo(0) : { row: r, col: 0 };
        break;
      case "End":
        next = e.ctrlKey ? verticalTo(count - 1) : { row: r, col: row.blocks.length - 1 };
        break;
      default:
        return;
    }
    e.preventDefault();
    if (next && !(next.row === r && next.col === c)) go(next);
  };

  const nowIn = nowMs >= day.startMs && nowMs < day.endMs;
  const onScroll = () => setScrollTop(viewport.current?.scrollTop ?? 0);

  return (
    <div className="timeline resource-timeline">
      <p className="field-help" id={`${uid}-help`}>
        {t("planning.resources.help")}
      </p>
      <h3 id={`${uid}-h`}>{fmt.day(day.startMs + 1)}</h3>
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
              {markers.map((m) => (
                <ExpiryMarker key={`${m.kind}-${m.ref_id}-${m.at}`} marker={m} left={((Date.parse(m.at) - day.startMs) / (day.endMs - day.startMs)) * 100} time={fmt.time(m.at)} />
              ))}
            </div>
          </div>
          <div className="rt-viewport" ref={viewport} onScroll={onScroll}>
            <div className="tl-body rt-body" style={{ blockSize: count * ROW_PX }}>
              {/* Le focus est porté par les blocs (tabindex itinérant), pas par la grille. */}
              {/* eslint-disable-next-line jsx-a11y/interactive-supports-focus */}
              <div role="grid" className="tl-grid" onKeyDown={onKeyDown} aria-labelledby={`${uid}-h`} aria-describedby={`${uid}-help`} aria-rowcount={count}>
                {indices.map((i) => {
                  const row = rowAt(i);
                  if (!row) return null;
                  const rowFmt = row.kind === "screen" && row.screen.timezone !== tz ? timeFormatter(i18n.language, row.screen.timezone) : fmt;
                  return (
                    <div role="row" className="tl-row rt-row" data-kind={row.kind === "lane" ? "lane" : "screen"} key={row.key} aria-rowindex={i + 1} style={{ insetBlockStart: i * ROW_PX, blockSize: ROW_PX }}>
                      <div role="rowheader" className="tl-label">
                        {row.kind === "screen" ? (
                          <>
                            <button
                              type="button"
                              className="btn btn-text link-like"
                              data-rt={`${i}:-1`}
                              tabIndex={tabbable.row === i && tabbable.col === -1 ? 0 : -1}
                              aria-label={t("planning.resources.open_screen", { name: row.screen.screen_name })}
                              onFocus={() => setActive({ row: i, col: -1 })}
                              onClick={() => onOpenScreen(row.screen.screen_id)}
                            >
                              {row.screen.screen_name}
                            </button>
                            {mixed ? <span className="meta tz-id">{row.screen.timezone}</span> : null}
                          </>
                        ) : (
                          <>
                            <strong>{row.lane.source.name ?? row.lane.content?.name}</strong>
                            {row.lane.source.priority_level ? <PriorityBadge level={row.lane.source.priority_level} value={row.lane.source.priority} /> : null}
                            <SourceOrigin source={row.lane.source} />
                          </>
                        )}
                      </div>
                      <div className="tl-track">
                        {row.blocks.map((b, c) => (
                          <SegmentBlock
                            key={b.key}
                            block={b}
                            fmt={rowFmt}
                            row={i}
                            tabbable={tabbable.row === i && tabbable.col === c}
                            selected={row.kind === "screen" && atMs !== null && atMs >= b.startMs && atMs < b.endMs}
                            creates={!!onEmpty && row.kind === "screen"}
                            onSelect={(blk) => (onEmpty && row.kind === "screen" && blk.segment?.state === "off" ? onEmpty(blk.startMs, blk.endMs) : onSelect(blk.startMs))}
                            onFocusBlock={() => setActive({ row: i, col: c })}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
              {nowIn ? <NowCursor ratio={(nowMs - day.startMs) / (day.endMs - day.startMs)} time={fmt.time(nowMs)} /> : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
