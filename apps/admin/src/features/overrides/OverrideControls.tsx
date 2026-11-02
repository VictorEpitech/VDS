import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import type { Screen } from "../../lib/queries";
import { minutesLeft, offlineOf, screensOf, useExtendOverride, useMinuteNow, useStopOverride, type Override } from "./overrides";

/** « 1 h 05 », « 12 min » : durée restante lisible, sans jargon. */
export function useDurationText() {
  const { t } = useTranslation();
  return (minutes: number): string => {
    if (minutes < 60) return t("overrides.duration.min", { count: minutes });
    if (minutes % 60 === 0) return t("overrides.duration.hours", { count: minutes / 60 });
    return t("overrides.duration.hours_min", { h: Math.floor(minutes / 60), m: minutes % 60 });
  };
}

export function useTimeText() {
  const { i18n } = useTranslation();
  return (iso: string, withDay = false): string => {
    const d = new Date(iso);
    const sameDay = d.toDateString() === new Date().toDateString();
    return new Intl.DateTimeFormat(i18n.language, withDay || !sameDay ? { dateStyle: "short", timeStyle: "short" } : { timeStyle: "short" }).format(d);
  };
}

/** « Forcé jusqu'à 14:30 » + temps restant, annoncé poliment et mis à jour à la minute seulement. */
export function OverrideUntil({ override }: { override: Override }) {
  const { t } = useTranslation();
  const now = useMinuteNow();
  const time = useTimeText();
  const dur = useDurationText();
  const scheduled = override.status === "scheduled";
  return (
    <>
      <span>{scheduled ? t("overrides.from", { when: time(override.starts_at) }) : t("overrides.until", { when: time(override.ends_at) })}</span>
      {!scheduled ? (
        <span className="meta" role="status" aria-live="polite">
          {" · "}
          {t("overrides.remaining", { duration: dur(minutesLeft(override.ends_at, now)) })}
        </span>
      ) : null}
    </>
  );
}

interface Props {
  override: Override;
  screens: readonly Screen[];
  /** Nom de la cible pour les libellés accessibles (par ex. le nom de l'écran). */
  label: string;
  /** Extinction forcée et portée organisation : réservées à l'administrateur (le serveur répond 403 sinon). */
  isAdmin?: boolean;
  onDone: (message: string) => void;
}

/** Prolonger (+1 h) et Arrêter (avec confirmation qui annonce les écrans hors ligne). */
export function OverrideControls({ override, screens, label, isAdmin = false, onDone }: Props) {
  const { t } = useTranslation();
  const time = useTimeText();
  const stop = useStopOverride();
  const extend = useExtendOverride();
  const [asking, setAsking] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const openRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) confirmRef.current?.focus();
  }, [asking]);

  const restricted = !isAdmin && (override.kind === "off" || override.target_type === "organization");
  const offline = offlineOf(screensOf(override, screens)).length;
  const end = time(override.ends_at);
  const cancel = () => {
    setAsking(false);
    queueMicrotask(() => openRef.current?.focus());
  };

  const onEscape = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Escape") cancel();
  };

  if (restricted) {
    return (
      <p className="meta">
        <Icon name="lock" /> {t("overrides.admin_only")}
      </p>
    );
  }

  return (
    <div className="override-controls">
      <div className="actions">
        <button type="button" className="btn btn-text" disabled={extend.isPending} aria-label={t("overrides.extend_for", { name: label })} onClick={() => void extend.mutateAsync(override).then((o) => onDone(t("overrides.extended", { name: label, when: time(o.ends_at) })), () => undefined)}>
          <Icon name="add" />
          {t("overrides.extend")}
        </button>
        <button ref={openRef} type="button" className="btn btn-text btn-danger" aria-expanded={asking} aria-label={t("overrides.stop_for", { name: label })} onClick={() => setAsking(true)}>
          <Icon name="revoke" />
          {t("overrides.stop")}
        </button>
      </div>
      {asking ? (
        <div className="selection-panel" role="group" aria-label={t("overrides.stop_for", { name: label })}>
          {offline > 0 ? (
            <p className="alert alert-warning" role="alert">
              <Icon name="warning" />
              <span>{t("overrides.stop_offline", { count: offline, when: end })}</span>
            </p>
          ) : null}
          <div className="actions">
            <button
              ref={confirmRef}
              type="button"
              className="btn btn-danger-filled"
              disabled={stop.isPending}
              onKeyDown={onEscape}
              onClick={() => {
                setAsking(false);
                // Le forçage disparaît des listes dès l'arrêt : le message passe par le parent, qui reste affiché.
                void stop.mutateAsync(override).then(() => onDone(offline > 0 ? t("overrides.stopped_offline", { name: label, count: offline, when: end }) : t("overrides.stopped", { name: label })), () => undefined);
              }}
            >
              <Icon name="revoke" />
              {t("overrides.stop_confirm")}
            </button>
            <button type="button" className="btn btn-text" onKeyDown={onEscape} onClick={cancel}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
      {extend.isError ? <ErrorAlert error={extend.error} /> : null}
      {stop.isError ? <ErrorAlert error={stop.error} /> : null}
    </div>
  );
}
