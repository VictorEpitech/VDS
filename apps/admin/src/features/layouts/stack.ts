import type { LayoutZone } from "./layouts";

/** Zones du dessus vers le dessous (z décroissant, puis ordre d'origine inversé). */
export function stackOrder<T extends { z: number }>(zones: readonly T[]): T[] {
  return zones
    .map((z, i) => ({ z, i }))
    .sort((a, b) => b.z.z - a.z.z || b.i - a.i)
    .map((x) => x.z);
}

/** Monte ou descend une zone d'un cran ; les `z` sont renumérotés 0..n-1 sans changer l'ordre relatif des autres. */
export function restack(zones: readonly LayoutZone[], key: string, dir: "up" | "down"): LayoutZone[] {
  const order = stackOrder(zones).reverse(); // du dessous au dessus
  const at = order.findIndex((z) => z.key === key);
  const to = dir === "up" ? at + 1 : at - 1;
  if (at < 0 || to < 0 || to >= order.length) return [...zones];
  const moved = [...order];
  [moved[at], moved[to]] = [moved[to]!, moved[at]!];
  const z = new Map(moved.map((m, i) => [m.key, i]));
  return zones.map((m) => ({ ...m, z: z.get(m.key) ?? m.z }));
}
