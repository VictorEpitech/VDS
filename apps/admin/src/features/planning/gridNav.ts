/** Navigation clavier de la grille (tabindex itinérant) : pure lecture du DOM, aucune règle de diffusion. */
const rowOf = (el: HTMLElement) => Number(el.dataset.row);
const startOf = (el: HTMLElement) => Number(el.dataset.start);

export function cellsOf(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-cell]"));
}

/**
 * ← → : segment précédent/suivant de la piste (au bord, jour voisin) ; ↑ ↓ : piste voisine, segment en regard ;
 * Début/Fin : bords de la piste ; PageUp/PageDown : même piste, jour précédent/suivant.
 */
export function nextCell(cells: HTMLElement[], from: HTMLElement, key: string, rowsPerDay: number): HTMLElement | null {
  const r = rowOf(from);
  const inRow = (n: number) => cells.filter((c) => rowOf(c) === n);
  const row = inRow(r);
  const i = row.indexOf(from);
  const maxRow = Math.max(...cells.map(rowOf));
  const facing = (n: number): HTMLElement | null => {
    const list = inRow(n);
    if (list.length === 0) return null;
    const before = list.filter((c) => startOf(c) <= startOf(from));
    return before.length > 0 ? before[before.length - 1]! : list[0]!;
  };
  switch (key) {
    case "ArrowRight":
      return row[i + 1] ?? inRow(r + rowsPerDay)[0] ?? null;
    case "ArrowLeft":
      return row[i - 1] ?? inRow(r - rowsPerDay).at(-1) ?? null;
    case "Home":
      return row[0] ?? null;
    case "End":
      return row.at(-1) ?? null;
    case "PageDown":
      return inRow(r + rowsPerDay)[0] ?? null;
    case "PageUp":
      return inRow(r - rowsPerDay)[0] ?? null;
    case "ArrowDown":
      for (let n = r + 1; n <= maxRow; n++) {
        const c = facing(n);
        if (c) return c;
      }
      return null;
    case "ArrowUp":
      for (let n = r - 1; n >= 0; n--) {
        const c = facing(n);
        if (c) return c;
      }
      return null;
    default:
      return null;
  }
}

export const NAV_KEYS = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "PageUp", "PageDown"];
