import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { LEVEL_DEFAULT, LEVELS, type PriorityLevel } from "./planning";

interface Props {
  /** Valeur numérique courante (texte du champ « Avancé »). */
  value: string;
  onChange: (value: string) => void;
  /** Niveau calculé par le serveur pour la valeur enregistrée. */
  savedLevel?: PriorityLevel;
  savedValue?: number;
}

/**
 * 3 niveaux (radiogroup) ; la valeur numérique est un champ du volet « Avancé » unique du formulaire
 * (rendu par `PriorityNumber`). Choisir un niveau envoie sa valeur par défaut ; une autre valeur affiche « Valeur personnalisée ».
 */
export function PriorityPicker({ value, onChange, savedLevel, savedValue }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const number = Number(value);
  // Le niveau d'une valeur enregistrée vient du serveur ; sinon seule l'égalité avec une valeur par défaut est reconnue.
  const checked: PriorityLevel | null =
    savedLevel && savedValue === number ? savedLevel : (LEVELS.find((l) => LEVEL_DEFAULT[l] === number && value.trim() !== "") ?? null);
  return (
    <fieldset className="field priority-picker">
      <legend>{t("schedules.priority.level.label")}</legend>
      <div className="seg" role="radiogroup" aria-label={t("schedules.priority.level.label")}>
        {LEVELS.map((level) => (
          <label key={level} className="seg-item">
            <input type="radio" name={`${uid}-level`} value={level} checked={checked === level} onChange={() => onChange(String(LEVEL_DEFAULT[level]))} />
            <span>
              {level === "background" ? <Icon name="priority_low" /> : level === "high" ? <Icon name="priority_high" /> : null}
              {t(`schedules.priority.level.${level}`)}
            </span>
          </label>
        ))}
      </div>
      <p className="field-help">{checked === null ? t("schedules.priority.custom", { value }) : t(`schedules.priority.level_help.${checked}`)}</p>
      <p className="field-help">{t("planning.precedence")}</p>
    </fieldset>
  );
}
