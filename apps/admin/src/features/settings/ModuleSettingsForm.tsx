import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { useApi } from "../../lib/api";
import type { JsonSchema } from "../../lib/content-types";
import { configKey, useConfig, type ModuleSettings } from "../../lib/queries";
import { SchemaForm } from "../schema-form/SchemaForm";
import { fieldsOf, validate, type FieldError, type Values } from "../schema-form/schema";

/**
 * Réglages d'un module : formulaire généré depuis son JSON Schema (jamais codé écran par écran).
 * Les valeurs fixées par l'environnement restent en lecture seule ; chaque valeur affiche sa provenance.
 */
export function ModuleSettingsForm({ moduleId, level, targetId }: { moduleId: string; level: "organization" | "site" | "group" | "screen"; targetId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["registry", "module-settings", moduleId],
    queryFn: async () => {
      const data = await api.get<ModuleSettings>(`/registry/modules/${moduleId}/settings`);
      for (const [lng, bundle] of Object.entries(data.i18n)) i18n.addResourceBundle(lng, moduleId, bundle, true, true);
      return data;
    },
  });
  const cfg = useConfig(level, targetId, moduleId);
  const [draft, setDraft] = useState<Values | null>(null);
  const [errors, setErrors] = useState<Record<string, FieldError>>({});
  const [saved, setSaved] = useState(false);
  const schema = settings.data?.config_schema as JsonSchema | undefined;

  const resolved = useMemo<Values>(() => {
    const out: Values = {};
    for (const [name, v] of Object.entries(cfg.data?.values ?? {})) if (!v.secret && v.value !== null && v.value !== undefined) out[name] = v.value;
    return out;
  }, [cfg.data]);
  const value = draft ?? resolved;
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
      <SchemaForm schema={schema} ns={moduleId} value={value} onChange={(v) => setDraft(v)} errors={errors} readOnly={locked} extra={extra} />
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("settings.saved")}</SuccessAlert> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending || draft === null} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}
