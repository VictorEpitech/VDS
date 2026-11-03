/** Géométrie des zones : fractions 0..1 à 4 décimales (comme l'API), pas de grille 1 %. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export const STEP = 0.01;
export const MIN_SIZE = 0.01;
const r4 = (n: number): number => Math.round(n * 10_000) / 10_000;

export const snap = (n: number, free: boolean): number => (free ? r4(n) : r4(Math.round(n / STEP) * STEP));

/** Rectangle ramené dans la surface (0..1) avec une taille minimale. */
export function clampRect(r: Rect): Rect {
  const w = Math.min(1, Math.max(MIN_SIZE, r.w));
  const h = Math.min(1, Math.max(MIN_SIZE, r.h));
  return { x: r4(Math.min(1 - w, Math.max(0, r.x))), y: r4(Math.min(1 - h, Math.max(0, r.y))), w: r4(w), h: r4(h) };
}

export type Arrow = "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown";
export const ARROWS: readonly string[] = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];

/** Flèches : déplacer d'un pas ; Maj + flèches : redimensionner (droite/bas agrandissent). */
export function nudge(r: Rect, key: Arrow, resize: boolean): Rect {
  const dx = key === "ArrowLeft" ? -STEP : key === "ArrowRight" ? STEP : 0;
  const dy = key === "ArrowUp" ? -STEP : key === "ArrowDown" ? STEP : 0;
  return clampRect(resize ? { ...r, w: r.w + dx, h: r.h + dy } : { ...r, x: r.x + dx, y: r.y + dy });
}

export type Handle = "move" | "resize";
/** Glisser : `dx`/`dy` en fractions de la surface depuis le départ du geste. */
export function drag(start: Rect, mode: Handle, dx: number, dy: number, free: boolean): Rect {
  if (mode === "move") return clampRect({ ...start, x: snap(start.x + dx, free), y: snap(start.y + dy, free) });
  return clampRect({ ...start, w: snap(start.w + dx, free), h: snap(start.h + dy, free) });
}

export const toPercent = (n: number): number => Math.round(n * 10_000) / 100;
export const fromPercent = (n: number): number => r4(n / 100);
/** Rectangle valide dans la surface (le serveur reste l'autorité). */
export const inBounds = (r: Rect): boolean => r.w > 0 && r.h > 0 && r.x >= 0 && r.y >= 0 && r.x + r.w <= 1.00001 && r.y + r.h <= 1.00001;
