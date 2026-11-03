import type { NavigateFunction } from "react-router-dom";
import type { IconName } from "../../components/Icon";

export interface PaletteEntry {
  id: string;
  kind: "navigate" | "create" | "action";
  /** Clé i18n du libellé affiché (jamais de texte en dur). */
  labelKey: string;
  labelParams?: Record<string, string>;
  /** Mots supplémentaires cherchés en plus du libellé (déjà traduits). */
  keywords: string[];
  icon: IconName;
  /** `admin` : réservé aux administrateurs ; `unrestricted` : masqué aux éditeurs limités à un périmètre. Le serveur reste juge. */
  requires?: "admin" | "unrestricted";
  run(nav: NavigateFunction): void;
}

type Source = () => PaletteEntry[];
const sources = new Set<Source>();
const listeners = new Set<() => void>();
let version = 0;

/** Une fonctionnalité déclare ses entrées ; renvoie la fonction qui les retire. */
export function registerPaletteSource(src: Source): () => void {
  sources.add(src);
  version += 1;
  listeners.forEach((l) => l());
  return () => {
    sources.delete(src);
    version += 1;
    listeners.forEach((l) => l());
  };
}

export function collectEntries(): PaletteEntry[] {
  const seen = new Set<string>();
  const out: PaletteEntry[] = [];
  for (const src of sources) {
    for (const e of src()) {
      if (seen.has(e.id)) continue;
      seen.add(e.id);
      out.push(e);
    }
  }
  return out;
}

export const subscribePalette = (l: () => void) => {
  listeners.add(l);
  return () => void listeners.delete(l);
};
export const paletteVersion = () => version;

/** Minuscules sans accents : « ecran » trouve « Écran ». */
export const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

export function matches(query: string, haystack: string): boolean {
  const h = fold(haystack);
  return fold(query).split(/\s+/).filter(Boolean).every((w) => h.includes(w));
}
