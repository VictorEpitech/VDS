import type { Orientation } from "./layouts";

interface Z {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
}

/** Vignette SVG des rectangles (couleurs tonales du thème, définies en CSS). Décorative : le contexte porte le texte. */
export function LayoutThumb({ zones, orientation }: { zones: readonly Z[]; orientation: Orientation }) {
  const [w, h] = orientation === "portrait" ? [90, 160] : [160, 90];
  const ordered = [...zones].sort((a, b) => a.z - b.z);
  return (
    <svg className="layout-thumb" data-orientation={orientation} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" focusable="false">
      <rect className="layout-thumb-bg" x={0} y={0} width={w} height={h} />
      {ordered.map((z, i) => (
        <rect key={z.key} className={`layout-thumb-zone zone-tone-${i % 4}`} x={z.x * w} y={z.y * h} width={z.w * w} height={z.h * h} />
      ))}
    </svg>
  );
}
