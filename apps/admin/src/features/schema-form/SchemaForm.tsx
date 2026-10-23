import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { JsonSchema } from "../../lib/content-types";
import type { Media } from "../../lib/queries";
import { fieldsOf, levelOf, type FieldError, type Values } from "./schema";

interface Props {
  schema: JsonSchema;
  /** Espace de noms i18next du module qui fournit les libellés du schéma. */
  ns: string;
  value: Values;
  onChange: (next: Values) => void;
  errors?: Record<string, FieldError>;
  /** Médias sélectionnables pour un champ `x-vds-media`. */
  mediaOf?: (kind: "image" | "video" | "document") => Media[];
  /** Champs non modifiables (ex. fixés par l'environnement) : affichés en lecture seule. */
  readOnly?: (name: string) => boolean;
  /** Contenu ajouté sous un champ (ex. badge de provenance et « revenir à l'héritage »). */
  extra?: (name: string) => ReactNode;
}

/** Liste de chaînes : une valeur par ligne (le texte brut est conservé pendant la saisie). */
function ListField({ common, value, onChange }: { common: Record<string, unknown>; value: string[]; onChange: (next: string[]) => void }) {
  const [raw, setRaw] = useState(value.join("\n"));
  return (
    <textarea
      {...common}
      rows={Math.min(Math.max(raw.split("\n").length, 2), 8)}
      spellCheck={false}
      autoComplete="off"
      value={raw}
      onChange={(e) => {
        setRaw(e.target.value);
        onChange(e.target.value.split("\n").map((x) => x.trim()).filter(Boolean));
      }}
    />
  );
}

/** Formulaire généré depuis un JSON Schema de module : champs `simple` visibles, volet « Avancé » pour les autres. */
export function SchemaForm({ schema, ns, value, onChange, errors = {}, mediaOf, readOnly, extra }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const fields = fieldsOf(schema);
  const simple = fields.filter(([, f]) => levelOf(f) === "simple");
  const advanced = fields.filter(([, f]) => levelOf(f) === "advanced");
  const advancedHasError = advanced.some(([name]) => errors[name]);
  const [openAdvanced, setOpenAdvanced] = useState(false);

  const set = (name: string, v: unknown) => {
    const next = { ...value };
    if (v === undefined || v === "") delete next[name];
    else next[name] = v;
    onChange(next);
  };

  const render = ([name, field]: [string, JsonSchema]) => {
    const id = `${uid}-${name}`;
    const err = errors[name];
    const required = schema.required?.includes(name) ?? false;
    const label = field.title ? t(field.title, { ns, defaultValue: name }) : name;
    const help = field.description ? t(field.description, { ns, defaultValue: "" }) : "";
    const describedBy = [help ? `${id}-help` : "", err ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
    const locked = readOnly?.(name) ?? false;
    const common = { id, name, "aria-invalid": err ? true : undefined, "aria-describedby": describedBy, required, disabled: locked || undefined };
    const v = value[name];
    let control;
    if (field["x-vds-media"]) {
      const options = mediaOf?.(field["x-vds-media"]) ?? [];
      control = (
        <select {...common} value={typeof v === "string" ? v : ""} onChange={(e) => set(name, e.target.value)}>
          <option value="">{t("form.choose")}</option>
          {options.map((m) => (
            <option key={m.id} value={m.id}>
              {m.title}
            </option>
          ))}
        </select>
      );
    } else if (field.enum) {
      const base = field.title?.replace(/\.title$/, "") ?? name;
      control = (
        <select {...common} value={typeof v === "string" ? v : ""} onChange={(e) => set(name, e.target.value)}>
          {required ? null : <option value="">{t("form.choose")}</option>}
          {field.enum.map((o) => (
            <option key={o} value={o}>
              {t([base, "enum", o].join("."), { ns, defaultValue: o })}
            </option>
          ))}
        </select>
      );
    } else if (field.type === "array") {
      const list = Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
      control = <ListField common={common} value={list} onChange={(next) => set(name, next)} />;
    } else if (field.type === "boolean") {
      return (
        <div className="field field-check" key={name}>
          <label htmlFor={id}>
            <input
              {...common}
              required={false}
              type="checkbox"
              checked={v === true}
              onChange={(e) => set(name, e.target.checked)}
            />
            <span>{label}</span>
          </label>
          {help ? (
            <p className="field-help" id={`${id}-help`}>
              {help}
            </p>
          ) : null}
          {extra?.(name)}
        </div>
      );
    } else if (field.type === "integer" || field.type === "number") {
      control = (
        <input
          {...common}
          type="number"
          inputMode="numeric"
          step={field.type === "integer" ? 1 : "any"}
          min={field.minimum}
          max={field.maximum}
          value={typeof v === "number" ? v : ""}
          onChange={(e) => set(name, e.target.value === "" ? undefined : Number(e.target.value))}
        />
      );
    } else {
      const url = field.pattern?.startsWith("^https");
      control = (
        <input
          {...common}
          type={url ? "url" : "text"}
          autoComplete="off"
          spellCheck={false}
          maxLength={field.maxLength}
          value={typeof v === "string" ? v : ""}
          onChange={(e) => set(name, e.target.value)}
        />
      );
    }
    return (
      <div className="field" key={name}>
        <label htmlFor={id}>
          {label}
          {required ? <span className="req" aria-hidden="true"> *</span> : null}
        </label>
        {control}
        {help ? (
          <p className="field-help" id={`${id}-help`}>
            {help}
          </p>
        ) : null}
        {extra?.(name)}
        {err ? (
          <p className="field-error" id={`${id}-error`}>
            <Icon name="warning" />
            <span>{t(`form.errors.${err.key}`, err.params)}</span>
          </p>
        ) : null}
      </div>
    );
  };

  return (
    <div className="schema-form">
      {simple.map(render)}
      {advanced.length > 0 ? (
        <details
          className="advanced"
          open={openAdvanced || advancedHasError}
          onToggle={(e) => setOpenAdvanced((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary>{t("form.advanced")}</summary>
          <div className="advanced-body">{advanced.map(render)}</div>
        </details>
      ) : null}
    </div>
  );
}
