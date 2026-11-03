import { createContext, useContext } from "react";
import { useTranslation } from "react-i18next";

export type Tone = "neutral" | "formal";

/** Ton déclaré par le module de thème actif (jeton `--vds-tone` de sa feuille) : jamais déduit du mode d'hébergement. */
export function readTone(doc: Document = document): Tone {
  const raw = getComputedStyle(doc.documentElement).getPropertyValue("--vds-tone").trim().replace(/["']/g, "");
  return raw === "formal" ? "formal" : "neutral";
}

const ToneContext = createContext<Tone | null>(null);
export const ToneProvider = ToneContext.Provider;

/** Ton courant : fourni par un `ToneProvider` (tests), sinon lu dans la feuille du thème. */
export function useTone(): Tone {
  const provided = useContext(ToneContext);
  return provided ?? readTone();
}

/** Texte traduit avec variante de ton : `<clé>_formal` si le thème est formel et qu'elle existe, sinon `<clé>`. */
export function useToneText(): (key: string, options?: Record<string, unknown>) => string {
  const { t, i18n } = useTranslation();
  const tone = useTone();
  return (key, options) => {
    const formal = `${key}_formal`;
    return tone === "formal" && i18n.exists(formal) ? t(formal, options) : t(key, options);
  };
}
