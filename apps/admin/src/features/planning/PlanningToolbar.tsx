import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { countTargets } from "../schedule/ScheduleForm";
import type { Group, Screen, Site } from "../../lib/queries";
import { LEVELS, VIEWS, type PlanningView, type Target, type TargetType } from "./planning";
import { PriorityBadge } from "./parts";

interface Props {
  target: Target | null;
  onTarget: (t: Target | null) => void;
  screens: Screen[];
  groups: Group[];
  sites: Site[];
  view: PlanningView;
  onView: (v: PlanningView) => void;
  date: string;
  onDate: (d: string) => void;
  /** Décale la date de n jours (vue Jour) ou de 7 (Semaine). */
  onStep: (direction: -1 | 1) => void;
  onNow: () => void;
  /** Fuseau du site si différent de celui du navigateur (ou plusieurs fuseaux). */
  tzNote: { tz: string; mixed: boolean } | null;
}

export function PlanningToolbar({ target, onTarget, screens, groups, sites, view, onView, date, onDate, onStep, onNow, tzNote }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const type: TargetType = target?.type ?? "screen";
  const options: { id: string; name: string }[] =
    type === "screen"
      ? screens.filter((s) => !s.archived_at).map((s) => ({ id: s.id, name: s.name }))
      : type === "group"
        ? groups.map((g) => ({ id: g.id, name: g.name }))
        : sites.filter((s) => !s.archived_at).map((s) => ({ id: s.id, name: s.name }));
  const pool = (ty: TargetType): { id: string }[] => (ty === "screen" ? screens.filter((s) => !s.archived_at) : ty === "group" ? groups : sites.filter((s) => !s.archived_at));
  const touched = target ? countTargets(target.type, target.id, screens) : null;

  return (
    <div className="planning-toolbar" role="group" aria-label={t("planning.toolbar")}>
      <fieldset className="field target">
        <legend>{t("schedules.target.label")}</legend>
        <div className="seg" role="radiogroup" aria-label={t("schedules.target.type")}>
          {(["screen", "group", "site"] as const).map((ty) => (
            <label key={ty} className="seg-item">
              <input
                type="radio"
                name={`${uid}-ttype`}
                value={ty}
                checked={type === ty}
                onChange={() => {
                  const first = pool(ty)[0];
                  onTarget(first ? { type: ty, id: first.id } : null);
                }}
              />
              <span>{t(`schedules.target.${ty}`)}</span>
            </label>
          ))}
        </div>
        <label htmlFor={`${uid}-target`} className="visually-hidden">
          {t(`schedules.target.${type}`)}
        </label>
        <select id={`${uid}-target`} value={target?.id ?? ""} onChange={(e) => onTarget(e.target.value ? { type, id: e.target.value } : null)}>
          <option value="">{t("form.choose")}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        {touched !== null ? (
          <p className="field-help" role="status">
            {t("schedules.target.touches", { count: touched })}
          </p>
        ) : null}
      </fieldset>

      <fieldset className="field">
        <legend>{t("planning.views.label")}</legend>
        <div className="seg" role="radiogroup" aria-label={t("planning.views.label")}>
          {VIEWS.map((v) => (
            <label key={v} className="seg-item">
              <input type="radio" name={`${uid}-view`} value={v} checked={view === v} onChange={() => onView(v)} />
              <span>{t(`planning.views.${v}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="planning-nav">
        <button type="button" className="btn btn-text" onClick={() => onStep(-1)} aria-label={view === "day" ? t("planning.nav.prev_day") : t("planning.nav.prev_week")}>
          <Icon name="back" />
          <span className="nav-text">{t("planning.nav.prev")}</span>
        </button>
        <div className="field planning-date">
          <label htmlFor={`${uid}-date`}>{t("planning.nav.date")}</label>
          <input id={`${uid}-date`} type="date" value={date} onChange={(e) => e.target.value && onDate(e.target.value)} />
        </div>
        <button type="button" className="btn btn-text" onClick={() => onStep(1)} aria-label={view === "day" ? t("planning.nav.next_day") : t("planning.nav.next_week")}>
          <span className="nav-text">{t("planning.nav.next")}</span>
          <Icon name="next" />
        </button>
        <button type="button" className="btn btn-filled" onClick={onNow}>
          <Icon name="schedule" />
          {t("planning.nav.now")}
        </button>
      </div>

      {tzNote ? (
        <p className="alert alert-info" role="status">
          <Icon name="detect" />
          <span>{tzNote.mixed ? t("planning.tz.mixed", { tz: tzNote.tz }) : t("planning.tz.differs", { tz: tzNote.tz })}</span>
        </p>
      ) : null}

      <details className="advanced legend">
        <summary>{t("planning.legend.title")}</summary>
        <div className="advanced-body">
          <ul className="list-reset legend-list">
            {LEVELS.map((l) => (
              <li key={l}>
                <PriorityBadge level={l} />
              </li>
            ))}
            <li><Icon name="overridden" /> {t("planning.legend.overridden")}</li>
            <li><Icon name="scheduled_off" /> {t("planning.legend.scheduled_off")}</li>
            <li><span className="legend-swatch" data-kind="off" aria-hidden="true" /> {t("planning.legend.off")}</li>
            <li><span className="legend-swatch" data-kind="masked" aria-hidden="true" /> {t("planning.legend.masked")}</li>
            <li><span className="legend-swatch" data-kind="draft" aria-hidden="true" /> {t("planning.legend.draft")}</li>
            <li><Icon name="expiry" /> {t("planning.legend.expiry")}</li>
          </ul>
          <p className="field-help">{t("planning.precedence")}</p>
        </div>
      </details>
    </div>
  );
}
