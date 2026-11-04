import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { joinBase } from "../../lib/url";
import { useRuntimeConfig } from "../../lib/runtime-config";
import type { Media } from "../../lib/queries";
import type { IconName } from "../../components/Icon";

const KIND_ICON: Record<string, IconName> = { image: "media", video: "video", document: "document" };

/** Aperçu : variante `thumb` servie sous la base ; à défaut, icône du type (décoratif, le titre est à côté). */
export function MediaThumb({ media, size = "card" }: { media: Pick<Media, "thumb" | "kind">; size?: "card" | "small" | "large" }) {
  const { basePath } = useRuntimeConfig();
  const { t } = useTranslation();
  return (
    <div className="media-thumb" data-size={size} data-kind={media.kind}>
      {media.thumb ? (
        <img src={joinBase(basePath, media.thumb)} alt="" loading="lazy" decoding="async" />
      ) : (
        <>
          <Icon name={KIND_ICON[media.kind] ?? "media"} />
          <span className="visually-hidden">{t("media.no_preview")}</span>
        </>
      )}
    </div>
  );
}
