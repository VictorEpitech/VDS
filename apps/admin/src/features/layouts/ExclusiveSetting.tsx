import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { useApi } from "../../lib/api";
import type { JsonSchema } from "../../lib/content-types";
import { configKey, useConfig } from "../../lib/queries";
import { SchemaForm } from "../schema-form/SchemaForm";
import { MAX_EXCLUSIVE_KEY } from "./warnings";

type Level = "organization" | "site" | "group" | "screen";

/** Extrait du schéma du noyau (`core`) : `player.max_exclusive_elements`, surchargeable à tous les niveaux. */
const EXCLUSIVE_SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    [MAX_EXCLUSIVE_KEY]: {
      type: "integer",
      minimum: 1,
      maximum: 4,
      title: "config.core.player.max_exclusive_elements.title",
      description: "config.core.player.max_exclusive_elements.description",
      default: 1,
      // Déjà rangé dans un volet repliable : le champ y est affiché sans second « Avancé ».
      "x-vds-level": "simple",
    },
  },
};

/** Réglage `core.player.max_exclusive_elements` : formulaire généré depuis le schéma, provenance et retour à l'héritage. */
function ExclusiveForm({ level, id }: { level: Level; id: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const session = useSession();
  const admin = session.data?.role === "admin";
  const cfg = useConfig(level, id, "core", admin);
  const [draft, setDraft] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const refresh = async () => {
    setDraft(null);
    await qc.invalidateQueries({ queryKey: configKey(level, id, "core") });
  };
  const save = useMutation({
    mutationFn: (value: number) => api.put(`/config/${level}/${id}/core/${MAX_EXCLUSIVE_KEY}`, { value }),
    onMutate: () => setSaved(false),
    onSuccess: async () => {
      setSaved(true);
      await refresh();
    },
  });
  const revert = useMutation({
    mutationFn: () => api.delete(`/config/${level}/${id}/core/${MAX_EXCLUSIVE_KEY}`),
    onMutate: () => setSaved(false),
    onSuccess: refresh,
  });
  if (!admin) return null;
  if (!cfg.data) return <QueryStatus query={cfg} />;
  const current = cfg.data.values[MAX_EXCLUSIVE_KEY];
  const resolved = typeof current?.value === "number" ? current.value : 1;
  const value = draft ?? resolved;
  const valid = Number.isInteger(value) && value >= 1 && value <= 4;
  const dirty = draft !== null && draft !== resolved;
  return (
    <form
      className="exclusive-setting"
      aria-label={t("layouts.exclusive.form_label")}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && valid) save.mutate(value);
      }}
    >
      <SchemaForm
        schema={EXCLUSIVE_SCHEMA}
        ns="translation"
        value={{ [MAX_EXCLUSIVE_KEY]: value }}
        onChange={(v) => setDraft(typeof v[MAX_EXCLUSIVE_KEY] === "number" ? v[MAX_EXCLUSIVE_KEY] : null)}
        extra={() =>
          current ? (
            <div className="provenance-row">
              <ProvenanceBadge origin={current.origin} level={level} />
              <RevertButton origin={current.origin} level={level} field={t("config.core.player.max_exclusive_elements.title")} pending={revert.isPending} onRevert={() => revert.mutate()} />
            </div>
          ) : null
        }
      />
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("settings.saved")}</SuccessAlert> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending || !dirty || !valid} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

/** Volet repliable : rien n'est chargé tant qu'il reste fermé (une carte par écran et par groupe). */
export function ExclusiveSetting({ level, id }: { level: Level; id: string }) {
  const { t } = useTranslation();
  const session = useSession();
  const [open, setOpen] = useState(false);
  if (session.data?.role !== "admin") return null;
  return (
    <details className="advanced exclusive-setting-box" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{t("layouts.exclusive.title")}</summary>
      {open ? <ExclusiveForm level={level} id={id} /> : null}
    </details>
  );
}
