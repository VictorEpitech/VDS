import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { Rule } from "../../lib/queries";
import { defaultRule, hasDay, ruleProblem, toggleDay } from "./rules";

interface Props {
  rules: Rule[];
  onChange: (rules: Rule[]) => void;
  /** Indices de créneaux à signaler (après une soumission refusée). */
  invalid: ReadonlySet<number>;
  legend: string;
  emptyHelp: string;
  help: string;
}

/** Créneaux hebdomadaires (jours + plage horaire), partagés par la programmation et les plans d'alimentation. */
export function RulesEditor({ rules, onChange, invalid, legend, emptyHelp, help }: Props) {
  const { t, i18n } = useTranslation();
  const day = (i: number, style: "short" | "long") => new Intl.DateTimeFormat(i18n.language, { weekday: style, timeZone: "UTC" }).format(Date.UTC(2024, 0, 1 + i));
  const setRule = (i: number, patch: Partial<Rule>) => onChange(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="field rules">
      <legend>{legend}</legend>
      <p className="field-help">{rules.length === 0 ? emptyHelp : help}</p>
      {rules.map((r, i) => {
        const problem = ruleProblem(r);
        return (
          <div key={i} className="rule card-inner" role="group" aria-label={t("schedules.rules.n", { n: i + 1 })}>
            <div className="days" role="group" aria-label={t("schedules.rules.days")}>
              {Array.from({ length: 7 }, (_, d) => (
                <button key={d} type="button" className="btn btn-day" aria-pressed={hasDay(r.weekdays, d)} aria-label={day(d, "long")} onClick={() => setRule(i, { weekdays: toggleDay(r.weekdays, d) })}>
                  {day(d, "short")}
                </button>
              ))}
            </div>
            <div className="times">
              <label>
                <span>{t("schedules.rules.start")}</span>
                <input type="time" value={r.start} onChange={(e) => setRule(i, { start: e.target.value })} aria-invalid={invalid.has(i) && problem === "range" ? true : undefined} />
              </label>
              <label>
                <span>{t("schedules.rules.end")}</span>
                <input type="time" value={r.end} onChange={(e) => setRule(i, { end: e.target.value })} aria-invalid={invalid.has(i) && problem === "range" ? true : undefined} />
              </label>
              <button type="button" className="btn btn-text btn-danger" onClick={() => onChange(rules.filter((_, j) => j !== i))}>
                <Icon name="delete" />
                {t("schedules.rules.remove")}
              </button>
            </div>
            {invalid.has(i) && problem ? (
              <p className="field-error" role="alert">
                <Icon name="warning" />
                <span>{t(`schedules.rules.problem.${problem}`)}</span>
              </p>
            ) : null}
          </div>
        );
      })}
      <div className="actions">
        <button type="button" className="btn btn-text" onClick={() => onChange([...rules, defaultRule()])}>
          <Icon name="add" />
          {t("schedules.rules.add")}
        </button>
        {rules.length > 0 ? (
          <button type="button" className="btn btn-text" onClick={() => onChange([])}>
            {t("schedules.rules.clear")}
          </button>
        ) : null}
      </div>
    </fieldset>
  );
}
