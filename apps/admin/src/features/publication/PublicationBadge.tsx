import { useTranslation } from "react-i18next";
import { Icon, type IconName } from "../../components/Icon";
import { viewOf, type Publication, type PublicationView } from "./publication";

const ICON: Record<PublicationView, IconName> = { never: "copy", changed: "warning", in_review: "processing", published: "check" };

/** État de publication : couleur + icône + libellé, jamais la couleur seule. */
export function PublicationBadge({ publication }: { publication: Pick<Publication, "state" | "published_rev"> | undefined | null }) {
  const { t } = useTranslation();
  if (!publication) return null;
  const view = viewOf(publication);
  return (
    <span className="status-badge" data-state={`pub-${view}`}>
      <Icon name={ICON[view]} />
      <span>{t(`publication.state.${view}`)}</span>
    </span>
  );
}
