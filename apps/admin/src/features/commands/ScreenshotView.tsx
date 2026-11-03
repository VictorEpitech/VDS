import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { useAppUrl } from "../../lib/runtime-config";
import type { Screen, ScreenPlayback } from "../../lib/queries";
import { canScreenshot, relativeAge, type Screenshot } from "./commands";

interface Props {
  screen: Screen;
  playback?: ScreenPlayback;
  /** Capture à montrer (par défaut la dernière de l'écran). */
  shot?: Screenshot | null;
  /** Taille réduite (grille du tableau de bord). */
  compact?: boolean;
}

/** Image d'une capture : servie `no-store` par le serveur, chemin relatif résolu sous VDS_BASE_PATH. */
export function ShotImage({ shot, alt, onLoad }: { shot: Screenshot; alt: string; onLoad?: () => void }) {
  const url = useAppUrl();
  return <img className="shot-img" src={url(shot.image_url)} alt={alt} width={shot.width} height={shot.height} loading="lazy" decoding="async" referrerPolicy="no-referrer" onLoad={onLoad} />;
}

/**
 * Dernière capture de l'écran, ou — si l'appareil ne sait pas capturer (Pi, web, Tizen) —
 * un aperçu théorique de la programmation, libellé comme tel : jamais présenté comme réel.
 */
export function ScreenshotView({ screen, playback, shot, compact = false }: Props) {
  const { t, i18n } = useTranslation();
  const capable = canScreenshot(screen);
  const last = shot === undefined ? screen.last_screenshot : shot;
  if (capable) {
    if (!last) {
      return (
        <figure className={`shot${compact ? " shot-compact" : ""}`} data-kind="none">
          <div className="shot-empty">
            <Icon name="media" />
            <span>{t("screens.commands.shot.none")}</span>
          </div>
        </figure>
      );
    }
    const age = relativeAge(last.captured_at, i18n.language);
    return (
      <figure className={`shot${compact ? " shot-compact" : ""}`} data-kind="real">
        <ShotImage shot={last} alt={t("screens.commands.shot.alt", { name: screen.name, age })} />
        <figcaption className="meta">
          <time dateTime={last.captured_at}>{t("screens.commands.shot.taken", { age })}</time>
          {last.trigger === "periodic" ? ` · ${t("screens.commands.shot.periodic")}` : ""}
        </figcaption>
      </figure>
    );
  }
  const what = playback && playback.source !== "none" ? [playback.layout_name, playback.playlist_name].filter(Boolean).join(" · ") : "";
  return (
    <figure className={`shot${compact ? " shot-compact" : ""}`} data-kind="theoretical" aria-label={t("screens.commands.shot.theoretical")}>
      <div className="shot-theory">
        <Icon name="schedule" />
        <span>{what || t("screens.commands.shot.no_item")}</span>
      </div>
      <figcaption className="meta">{t("screens.commands.shot.theoretical")}</figcaption>
    </figure>
  );
}
