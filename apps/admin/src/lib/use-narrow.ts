import { useEffect, useState } from "react";

/** `true` sous 40 rem (≈ 640 px) : l'édition au doigt du canevas est désactivée (règle 375 px). */
export function useNarrow(): boolean {
  const query = "(max-width: 39.99rem)";
  const get = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}
