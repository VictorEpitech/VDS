import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { fromParts, toParts } from "../../lib/media-view";

interface Props {
  /** Instant ISO courant, ou null. */
  value: string | null;
  pending?: boolean;
  /** Propose « Retirer l'expiration » même sans valeur courante (actions groupées). */
  allowClear?: boolean;
  /** Enregistre la nouvelle échéance (null = retirer). */
  onSave: (iso: string | null) => void;
}

/** Expiration : date + heure locales ; l'heure par défaut est 23:59. */
export function ExpiryField({ value, pending = false, allowClear = false, onSave }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const initial = toParts(value);
  const [date, setDate] = useState(initial.date);
  const [time, setTime] = useState(initial.time);
  const iso = fromParts(date, time);
  const changed = iso !== null && iso !== value;
  return (
    <fieldset className="field expiry-field">
      <legend>{t("expiry.label")}</legend>
      <div className="expiry-inputs">
        <div className="field">
          <label htmlFor={`${uid}-d`}>{t("expiry.date")}</label>
          <input id={`${uid}-d`} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-t`}>{t("expiry.time")}</label>
          <input id={`${uid}-t`} type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
      </div>
      <div className="actions">
        <button type="button" className="btn btn-filled" disabled={!changed || pending} onClick={() => onSave(iso)}>
          {t("common.save")}
        </button>
        {value || allowClear ? (
          <button
            type="button"
            className="btn btn-text"
            disabled={pending}
            onClick={() => {
              setDate("");
              onSave(null);
            }}
          >
            {t("expiry.clear")}
          </button>
        ) : null}
      </div>
      <p className="field-help">{t("expiry.help")}</p>
    </fieldset>
  );
}
