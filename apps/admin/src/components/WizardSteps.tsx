import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";

/** Fil d'étapes d'un assistant : numéro ou coche, étape courante marquée par `aria-current` (jamais par la couleur seule). */
export function WizardSteps({ label, steps, current }: { label: string; steps: string[]; current: number }) {
  const { t } = useTranslation();
  return (
    <ol className="wizard-steps list-reset" aria-label={label}>
      {steps.map((name, i) => {
        const n = i + 1;
        return (
          <li key={name} aria-current={n === current ? "step" : undefined} data-done={n < current ? "true" : undefined}>
            <span className="wizard-num">{n < current ? <Icon name="check" /> : n}</span>
            <span>{name}</span>
            {n === current ? <span className="visually-hidden">{t("wizard.current")}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
