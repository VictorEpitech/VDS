import { useTranslation } from "react-i18next";

/** Ligne verticale « Maintenant 10:32 » : décorative (`aria-hidden`), l'information est aussi dans l'inspecteur. */
export function NowCursor({ ratio, time }: { ratio: number; time: string }) {
  const { t } = useTranslation();
  return (
    <div className="tl-now" aria-hidden="true" style={{ "--now": ratio } as React.CSSProperties}>
      <span className="tl-now-label">{t("planning.now_at", { time })}</span>
    </div>
  );
}
