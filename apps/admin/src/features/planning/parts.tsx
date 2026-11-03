import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { SourceRef, PriorityLevel } from "./planning";

/** Niveau de priorité : badge texte + icône (jamais la couleur seule) ; l'entier en sous-texte (« Prioritaire · 120 »). */
export function PriorityBadge({ level, value, compact = false }: { level: PriorityLevel; value?: number | null; compact?: boolean }) {
  const { t } = useTranslation();
  const label = t(`schedules.priority.level.${level}`);
  return (
    <span className="level-badge" data-level={level}>
      {level === "background" ? <Icon name="priority_low" /> : level === "high" ? <Icon name="priority_high" /> : null}
      <span className={compact ? "level-text" : undefined}>{label}</span>
      {value !== undefined && value !== null && !compact ? <span className="level-value">{`· ${value}`}</span> : null}
    </span>
  );
}

/** Libellés de provenance d'une source, tirés des champs renvoyés par le serveur. */
export function useSourceText() {
  const { t } = useTranslation();
  const scope = (src: SourceRef): string => {
    if (!src.target_type) return "";
    const type = t(`planning.scope.${src.target_type}`);
    return src.target_type === "organization" ? type : `${type} ${src.target_name ?? ""}`.trim();
  };
  const level = (src: SourceRef): string => (src.priority_level ? t(`schedules.priority.level.${src.priority_level}`) : "");
  return {
    scope,
    level,
    /** « Accueil (site Moulins, Normale) » */
    label: (src: SourceRef): string => {
      if (src.kind === "none") return t("planning.source.none");
      const name = src.name ?? t(`planning.source.kind.${src.kind}`);
      const inner = [scope(src), level(src)].filter(Boolean).join(", ");
      return inner ? `${name} (${inner})` : name;
    },
    /** « Défini ici » ou « Hérité du site Moulins » */
    origin: (src: SourceRef): string => (src.inherited ? t("planning.source.inherited", { scope: scope(src) }) : t("planning.source.here")),
  };
}

/** Provenance d'un segment : icône + libellé (hérité ou défini ici). */
export function SourceOrigin({ source }: { source: SourceRef }) {
  const { origin } = useSourceText();
  if (source.kind === "none") return null;
  return (
    <span className="provenance" data-origin={source.inherited ? "inherited" : "here"}>
      <Icon name="inherit" />
      <span>{origin(source)}</span>
    </span>
  );
}
