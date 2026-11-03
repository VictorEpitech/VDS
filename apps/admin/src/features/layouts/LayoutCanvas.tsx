import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import { ARROWS, drag, nudge, toPercent, type Arrow, type Handle, type Rect } from "./geometry";
import type { LayoutZone, Orientation } from "./layouts";

interface Props {
  zones: readonly LayoutZone[];
  orientation: Orientation;
  background: string | null;
  selected: string | null;
  onSelect: (key: string) => void;
  onChange: (key: string, r: Rect) => void;
  /** Lecture seule (petits écrans : pas d'édition au doigt du canevas). */
  readOnly: boolean;
  playlistName: (id: string | null) => string;
}

interface Gesture {
  key: string;
  mode: Handle;
  startX: number;
  startY: number;
  rect: Rect;
}

const rectOf = (z: LayoutZone): Rect => ({ x: z.x, y: z.y, w: z.w, h: z.h });

/**
 * Canevas au ratio de l'écran. Souris : glisser pour déplacer, poignée pour redimensionner (pas de 1 %, Maj : libre).
 * Clavier : flèches = déplacer, Maj + flèches = redimensionner. Les coordonnées restent éditables dans le panneau Propriétés.
 */
export function LayoutCanvas({ zones, orientation, background, selected, onSelect, onChange, readOnly, playlistName }: Props) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const [announce, setAnnounce] = useState("");

  const say = (z: LayoutZone, r: Rect) => setAnnounce(t("layouts.canvas.position", { name: z.name, x: toPercent(r.x), y: toPercent(r.y), w: toPercent(r.w), h: toPercent(r.h) }));

  const start = (e: PointerEvent, z: LayoutZone, mode: Handle) => {
    onSelect(z.key);
    if (readOnly || e.button !== 0) return;
    e.stopPropagation();
    gesture.current = { key: z.key, mode, startX: e.clientX, startY: e.clientY, rect: rectOf(z) };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const move = (e: PointerEvent, z: LayoutZone) => {
    const g = gesture.current;
    const box = ref.current?.getBoundingClientRect();
    if (!g || g.key !== z.key || !box || box.width === 0 || box.height === 0) return;
    const next = drag(g.rect, g.mode, (e.clientX - g.startX) / box.width, (e.clientY - g.startY) / box.height, e.shiftKey);
    onChange(z.key, next);
    say(z, next);
  };
  const end = () => {
    gesture.current = null;
  };
  const key = (e: KeyboardEvent, z: LayoutZone) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(z.key);
      return;
    }
    if (readOnly || !ARROWS.includes(e.key)) return;
    e.preventDefault();
    const next = nudge(rectOf(z), e.key as Arrow, e.shiftKey);
    onChange(z.key, next);
    say(z, next);
  };

  return (
    <div className="layout-canvas-wrap" data-orientation={orientation}>
      <div
        ref={ref}
        className="layout-canvas"
        data-orientation={orientation}
        data-readonly={readOnly ? "true" : undefined}
        style={background ? { backgroundColor: background } : undefined}
        role="group"
        aria-label={t("layouts.canvas.label")}
        aria-describedby="layout-canvas-help"
      >
        {zones.map((z, i) => (
          <div
            key={z.key}
            role="button"
            tabIndex={0}
            aria-pressed={selected === z.key}
            aria-label={t("layouts.canvas.zone", { name: z.name, x: toPercent(z.x), y: toPercent(z.y), w: toPercent(z.w), h: toPercent(z.h) })}
            className={`layout-zone zone-tone-${i % 4}`}
            data-selected={selected === z.key ? "true" : undefined}
            style={{ left: `${z.x * 100}%`, top: `${z.y * 100}%`, width: `${z.w * 100}%`, height: `${z.h * 100}%`, zIndex: z.z + 1 }}
            onPointerDown={(e) => start(e, z, "move")}
            onPointerMove={(e) => move(e, z)}
            onPointerUp={end}
            onPointerCancel={end}
            onKeyDown={(e) => key(e, z)}
            onFocus={() => onSelect(z.key)}
          >
            <span className="layout-zone-name">{z.name}</span>
            <span className="layout-zone-playlist meta">{playlistName(z.playlist_id)}</span>
            {!readOnly ? <span className="layout-zone-handle" aria-hidden="true" onPointerDown={(e) => start(e, z, "resize")} /> : null}
          </div>
        ))}
      </div>
      <p className="field-help" id="layout-canvas-help">
        {readOnly ? t("layouts.canvas.readonly") : t("layouts.canvas.help")}
      </p>
      <p className="visually-hidden" role="status" aria-live="polite">
        {announce}
      </p>
    </div>
  );
}
