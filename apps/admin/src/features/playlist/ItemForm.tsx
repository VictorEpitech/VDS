import { useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useContentTypes, type ContentType } from "../../lib/content-types";
import type { Media, PlaylistItem } from "../../lib/queries";
import { SchemaForm } from "../schema-form/SchemaForm";
import { initialValues, toProps, validate, type FieldError, type Values } from "../schema-form/schema";

export interface ItemDraft {
  content_type: string;
  props: Values;
  duration_ms: number | null;
  enabled: boolean;
}

interface Props {
  /** Élément édité ; absent = ajout. */
  item?: PlaylistItem;
  media: Media[];
  pending: boolean;
  error: unknown;
  onSubmit: (draft: ItemDraft) => void;
  onCancel: () => void;
}

/** Ajout ou modification d'un élément : champs générés depuis le schéma d'élément du type de contenu (registre). */
export function ItemForm(props: Props) {
  const { types, query } = useContentTypes();
  if (!query.isSuccess) return <QueryStatus query={query} />;
  return <ItemFormFields {...props} types={types} />;
}

function ItemFormFields({ item, media, pending, error, onSubmit, onCancel, types }: Props & { types: readonly ContentType[] }) {
  const contentTypeById = (id: string) => types.find((c) => c.id === id);
  const { t, i18n } = useTranslation();
  const uid = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [typeId, setTypeId] = useState(item?.content_type ?? types[0]?.id ?? "");
  const type = contentTypeById(typeId);
  const [values, setValues] = useState<Values>(() => (type ? initialValues(type.schema, item?.props) : {}));
  const [seconds, setSeconds] = useState(item?.duration_ms ? String(item.duration_ms / 1000) : "");
  const [enabled, setEnabled] = useState(item?.enabled ?? true);
  const [errors, setErrors] = useState<Record<string, FieldError>>({});
  const [durationError, setDurationError] = useState(false);
  const lang = i18n.language === "en" ? "en" : "fr";

  const changeType = (id: string) => {
    setTypeId(id);
    const next = contentTypeById(id);
    setValues(next ? initialValues(next.schema) : {});
    setErrors({});
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!type) return;
    const found = validate(type.schema, values);
    const dur = seconds.trim() === "" ? null : Number(seconds);
    const badDuration = dur !== null && (!Number.isFinite(dur) || dur < 1 || dur > 3600);
    setErrors(found);
    setDurationError(badDuration);
    if (Object.keys(found).length > 0 || badDuration) {
      queueMicrotask(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    onSubmit({ content_type: typeId, props: toProps(type.schema, values), duration_ms: dur === null ? null : Math.round(dur * 1000), enabled });
  };

  return (
    <form ref={formRef} className="card item-form" onSubmit={submit} noValidate aria-label={item ? t("playlist.item.edit") : t("playlist.item.add")}>
      <h3>{item ? t("playlist.item.edit") : t("playlist.item.add")}</h3>
      <div className="field">
        <label htmlFor={`${uid}-type`}>{t("playlist.item.type")}</label>
        <select id={`${uid}-type`} value={typeId} disabled={Boolean(item)} onChange={(e) => changeType(e.target.value)}>
          {types.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name[lang]}
            </option>
          ))}
        </select>
      </div>
      {type ? (
        <SchemaForm
          key={typeId}
          schema={type.schema}
          ns={type.id}
          value={values}
          onChange={setValues}
          errors={errors}
          mediaOf={(kind) => media.filter((m) => m.kind === kind && m.status === "ready")}
        />
      ) : null}
      <div className="field">
        <label htmlFor={`${uid}-duration`}>{t("playlist.item.duration")}</label>
        <input
          id={`${uid}-duration`}
          type="number"
          inputMode="numeric"
          min={1}
          max={3600}
          step={1}
          value={seconds}
          placeholder={t("playlist.item.duration_default")}
          onChange={(e) => setSeconds(e.target.value)}
          aria-invalid={durationError ? true : undefined}
          aria-describedby={`${uid}-duration-help`}
        />
        <p className="field-help" id={`${uid}-duration-help`}>
          {t("playlist.item.duration_help")}
        </p>
        {durationError ? (
          <p className="field-error">
            <Icon name="warning" />
            <span>{t("form.errors.range", { min: 1, max: 3600 })}</span>
          </p>
        ) : null}
      </div>
      {item ? (
        <div className="field field-check">
          <label>
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>{t("playlist.item.enabled")}</span>
          </label>
        </div>
      ) : null}
      {error ? <ErrorAlert error={error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={pending} aria-busy={pending}>
          <Icon name={item ? "online" : "add"} />
          {item ? t("common.save") : t("playlist.item.add_submit")}
        </button>
        <button type="button" className="btn btn-text" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
