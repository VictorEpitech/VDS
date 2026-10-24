import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { ApiError, useApi } from "../../lib/api";
import type { JsonSchema } from "../../lib/content-types";
import { configKey, useConfig, type ModuleSettings } from "../../lib/queries";
import { SchemaForm } from "../schema-form/SchemaForm";
import { fieldsOf, validate, type FieldError, type Values } from "../schema-form/schema";

/**
 * Réglages d'un module : formulaire généré depuis son JSON Schema (jamais codé écran par écran).
 * Les valeurs fixées par l'environnement restent en lecture seule ; chaque valeur affiche sa provenance.
 */
type Level = "organization" | "site" | "group" | "screen";

/** Schéma et traductions d'un module (chargés à chaud, ajoutés à l'espace de noms du module). Pas de nouvel essai sur 404. */
export function useModuleSettings(moduleId: string, enabled = true) {
  const { i18n } = useTranslation();
  const api = useApi();
  return useQuery({
    enabled,
    queryKey: ["registry", "module-settings", moduleId],
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
    queryFn: async () => {
      const data = await api.get<ModuleSettings>(`/registry/modules/${moduleId}/settings`);
      for (const [lng, bundle] of Object.entries(data.i18n)) i18n.addResourceBundle(lng, moduleId, bundle, true, true);
      return data;
    },
  });
}

/** Ne garde que les clés surchargeables à ce niveau (`x-vds-scopes`). */
function scopedSchema(schema: JsonSchema, level: Level): JsonSchema {
  const props = Object.entries(schema.properties ?? {}).filter(([, f]) => !f["x-vds-scopes"] || f["x-vds-scopes"].includes(level));
  return { ...schema, properties: Object.fromEntries(props) };
}

export function ModuleSettingsForm({
  moduleId,
  level,
  targetId,
  proposal,
  onSaved,
}: {
  moduleId: string;
  level: Level;
  targetId: string;
  /** Valeurs proposées (ex. détection) : préremplies, enregistrées seulement après validation par l'utilisateur. */
  proposal?: Values;
  onSaved?: () => void;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const settings = useModuleSettings(moduleId);
  const cfg = useConfig(level, targetId, moduleId);
  const [draft, setDraft] = useState<Values | null>(null);
  const [errors, setErrors] = useState<Record<string, FieldError>>({});
  const [saved, setSaved] = useState(false);
  const schema = useMemo(() => (settings.data ? scopedSchema(settings.data.config_schema as JsonSchema, level) : undefined), [settings.data, level]);

  const resolved = useMemo<Values>(() => {
    const out: Values = {};
    for (const [name, v] of Object.entries(cfg.data?.values ?? {})) if (!v.secret && v.value !== null && v.value !== undefined) out[name] = v.value;
    return out;
  }, [cfg.data]);
  const proposed = useMemo<Values>(() => {
    const out: Values = {};
    for (const [name, v] of Object.entries(proposal ?? {})) if (schema?.properties?.[name] && !schema.properties[name]["x-vds-secret"] && !schema.properties[name]["x-vds-env"]) out[name] = v;
    return out;
  }, [proposal, schema]);
  const hasProposal = Object.keys(proposed).length > 0;
  const value = draft ?? { ...resolved, ...proposed };
  const locked = (name: string) => Boolean(schema?.properties?.[name]?.["x-vds-env"]) || Boolean(schema?.properties?.[name]?.["x-vds-secret"]);

  const refresh = () => qc.invalidateQueries({ queryKey: configKey(level, targetId, moduleId) });
  const save = useMutation({
    mutationFn: async (values: Values) => {
      for (const [name] of fieldsOf(schema ?? {})) {
        if (locked(name)) continue;
        if (JSON.stringify(values[name]) === JSON.stringify(resolved[name])) continue;
        if (values[name] === undefined) await api.delete(`/config/${level}/${targetId}/${moduleId}/${name}`);
        else await api.put(`/config/${level}/${targetId}/${moduleId}/${name}`, { value: values[name] });
      }
    },
    onMutate: () => setSaved(false),
    onSuccess: async () => {
      setDraft(null);
      setSaved(true);
      onSaved?.();
      await refresh();
    },
  });
  const revert = useMutation({
    mutationFn: (name: string) => api.delete(`/config/${level}/${targetId}/${moduleId}/${name}`),
    onMutate: () => setSaved(false),
    onSuccess: async () => {
      setDraft(null);
      await refresh();
    },
  });

  if (!settings.data || !schema) return <QueryStatus query={settings} />;
  if (!cfg.data) return <QueryStatus query={cfg} />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const found = validate(schema, value);
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate(value);
  };
  const extra = (name: string) => {
    const v = cfg.data?.values[name];
    if (!v) return null;
    const env = schema.properties?.[name]?.["x-vds-env"];
    return (
      <div className="provenance-row">
        <ProvenanceBadge origin={v.origin} level={level} />
        {env ? <span className="meta">{t("settings.env_var", { name: env })}</span> : null}
        <RevertButton
          origin={v.origin}
          level={level}
          field={t(schema.properties?.[name]?.title ?? name, { ns: moduleId, defaultValue: name })}
          pending={revert.isPending}
          onRevert={() => revert.mutate(name)}
        />
      </div>
    );
  };

  return (
    <form className="card" onSubmit={submit} noValidate aria-label={t("settings.form_label")}>
      {hasProposal && draft === null ? (
        <p className="alert alert-info" role="status">
          <Icon name="detect" />
          <span>{t("settings.proposal")}</span>
        </p>
      ) : null}
      <SchemaForm schema={schema} ns={moduleId} value={value} onChange={(v) => setDraft(v)} errors={errors} readOnly={locked} extra={extra} secretSet={(n) => cfg.data?.values[n]?.is_set === true} />
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("settings.saved")}</SuccessAlert> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending || (draft === null && !hasProposal)} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}
