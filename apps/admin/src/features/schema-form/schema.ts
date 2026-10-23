import type { JsonSchema } from "../../lib/content-types";

export type Values = Record<string, unknown>;
export interface FieldError {
  key: string;
  params?: Record<string, unknown>;
}

/** Champs d'un schéma d'objet, dans l'ordre déclaré. */
export function fieldsOf(schema: JsonSchema): [string, JsonSchema][] {
  return Object.entries(schema.properties ?? {});
}

export const levelOf = (field: JsonSchema): "simple" | "advanced" => (field["x-vds-level"] === "advanced" ? "advanced" : "simple");

/** Valeurs initiales : celles de l'élément édité, complétées par les valeurs par défaut du schéma. */
export function initialValues(schema: JsonSchema, existing: Values = {}): Values {
  const out: Values = {};
  for (const [name, field] of fieldsOf(schema)) {
    const v = existing[name] ?? field.default;
    if (v !== undefined) out[name] = v;
  }
  return out;
}

const isEmpty = (v: unknown) => v === undefined || v === null || v === "";

/** Validation côté client (le serveur reste l'autorité) : obligatoire, bornes, motif, longueur. */
export function validate(schema: JsonSchema, values: Values): Record<string, FieldError> {
  const errors: Record<string, FieldError> = {};
  for (const [name, field] of fieldsOf(schema)) {
    const v = values[name];
    if (isEmpty(v)) {
      if (schema.required?.includes(name)) errors[name] = { key: "required" };
      continue;
    }
    if (typeof v === "number") {
      if ((field.minimum !== undefined && v < field.minimum) || (field.maximum !== undefined && v > field.maximum)) {
        errors[name] = { key: "range", params: { min: field.minimum ?? "", max: field.maximum ?? "" } };
      }
    } else if (typeof v === "string") {
      if (field.pattern && !new RegExp(field.pattern).test(v)) errors[name] = { key: "pattern" };
      else if (field.maxLength !== undefined && v.length > field.maxLength) {
        errors[name] = { key: "too_long", params: { max: field.maxLength } };
      }
    }
  }
  return errors;
}

/** Propriétés envoyées à l'API : sans valeur vide. */
export function toProps(schema: JsonSchema, values: Values): Values {
  const out: Values = {};
  for (const [name] of fieldsOf(schema)) {
    const v = values[name];
    if (!isEmpty(v)) out[name] = typeof v === "string" ? v.trim() : v;
  }
  return out;
}
