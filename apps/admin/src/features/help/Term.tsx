import { useEffect, useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { GlossaryKey } from "./topics";

/**
 * Terme du glossaire : bouton (focus clavier, toucher) dont la définition s'affiche en infobulle au survol,
 * au focus ou après un appui ; Échap la referme. La définition est reliée par `aria-describedby`.
 */
export function Term({ k, children }: { k: GlossaryKey; children: ReactNode }) {
  const { t } = useTranslation();
  const id = useId();
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const shown = hover || focus || pinned;
  useEffect(() => {
    if (!shown) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setHover(false);
        setPinned(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [shown]);
  return (
    <span className="term-wrap" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button type="button" className="term" aria-describedby={id} onFocus={() => setFocus(true)} onBlur={() => { setFocus(false); setPinned(false); }} onClick={() => setPinned((p) => !p)}>
        {children}
      </button>
      <span id={id} role="tooltip" className="term-tip" hidden={!shown}>
        {t(`glossary.${k}.definition`)}
      </span>
    </span>
  );
}
