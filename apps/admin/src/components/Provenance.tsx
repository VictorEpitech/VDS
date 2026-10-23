import { useTranslation } from "react-i18next";
import type { Origin } from "../lib/queries";
import { Icon } from "./Icon";

/** Provenance d'une valeur résolue : icône + libellé ("Hérité du site Moulins"), jamais la couleur seule. */
export function ProvenanceBadge({ origin, level }: { origin: Origin; level: string }) {
  const { t } = useTranslation();
  const here = origin.level === level;
  const label = here
    ? t("provenance.here")
    : origin.level === "default" || origin.level === "env"
      ? t(`provenance.${origin.level}`)
      : t("provenance.inherited", { level: origin.level, name: origin.name ?? "" });
  return (
    <span className="provenance" data-origin={here ? "here" : origin.level}>
      <Icon name="inherit" />
      <span>{label}</span>
    </span>
  );
}

/** Action « revenir à l'héritage » : visible seulement quand le niveau courant porte une surcharge. */
export function RevertButton({ origin, level, field, pending, onRevert }: { origin: Origin; level: string; field: string; pending?: boolean; onRevert: () => void }) {
  const { t } = useTranslation();
  if (origin.level !== level) return null;
  return (
    <button type="button" className="btn btn-text" disabled={pending} onClick={onRevert} aria-label={t("provenance.revert_for", { field })}>
      <Icon name="rotate" />
      {t("provenance.revert")}
    </button>
  );
}
