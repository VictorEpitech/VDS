import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { ConfirmAction, ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { useApi } from "../../lib/api";
import type { JsonSchema } from "../../lib/content-types";
import { configKey, useConfig } from "../../lib/queries";
import { SchemaForm } from "../schema-form/SchemaForm";

type Level = "organization" | "site" | "group" | "screen";
const KEY = "player.kiosk";

/** Extrait du schéma du noyau (`core`) : la clé `player.kiosk`, surchargeable au groupe et à l'écran seulement. */
const KIOSK_SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    [KEY]: {
      type: "string",
      enum: ["locked", "unlocked"],
      title: "config.core.player.kiosk.title",
      description: "config.core.player.kiosk.description",
      default: "locked",
      "x-vds-level": "advanced",
      "x-vds-scopes": ["group", "screen"],
    },
  },
};

/** Réglage `core.player.kiosk` : formulaire généré depuis le schéma, groupe et écran seulement, avec provenance. */
export function KioskSetting({ level, id }: { level: Level; id: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const session = useSession();
  const allowed = KIOSK_SCHEMA.properties?.[KEY]?.["x-vds-scopes"]?.includes(level) ?? false;
  const cfg = useConfig(level, id, "core", allowed && session.data?.role === "admin");
  const [draft, setDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const refresh = async () => {
    setDraft(null);
    await qc.invalidateQueries({ queryKey: configKey(level, id, "core") });
    await qc.invalidateQueries({ queryKey: ["screens"] });
  };
  const save = useMutation({
    mutationFn: (value: string) => api.put(`/config/${level}/${id}/core/${KEY}`, { value }),
    onMutate: () => setSaved(false),
    onSuccess: async () => {
      setSaved(true);
      await refresh();
    },
  });
  const revert = useMutation({
    mutationFn: () => api.delete(`/config/${level}/${id}/core/${KEY}`),
    onMutate: () => setSaved(false),
    onSuccess: refresh,
  });
  if (!allowed || session.data?.role !== "admin") return null;
  if (!cfg.data) return <QueryStatus query={cfg} />;
  const current = cfg.data.values[KEY];
  const resolved = current?.value === "unlocked" ? "unlocked" : "locked";
  const value = draft ?? resolved;
  const dirty = draft !== null && draft !== resolved;
  const unlocking = dirty && value === "unlocked";
  return (
    <form
      className="kiosk-setting"
      aria-label={t("kiosk.setting.form_label")}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && !unlocking) save.mutate(value);
      }}
    >
      <SchemaForm
        schema={KIOSK_SCHEMA}
        ns="translation"
        value={{ [KEY]: value }}
        onChange={(v) => setDraft(typeof v[KEY] === "string" ? v[KEY] : null)}
        extra={() =>
          current ? (
            <div className="provenance-row">
              <ProvenanceBadge origin={current.origin} level={level} />
              <RevertButton origin={current.origin} level={level} field={t("config.core.player.kiosk.title")} pending={revert.isPending} onRevert={() => revert.mutate()} />
            </div>
          ) : null
        }
      />
      {unlocking ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("kiosk.setting.unlock_warning")}</span>
        </p>
      ) : null}
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("settings.saved")}</SuccessAlert> : null}
      <div className="actions">
        {unlocking ? (
          <ConfirmAction icon="unlock" label={t("kiosk.setting.unlock")} confirmLabel={t("kiosk.setting.unlock_confirm")} pending={save.isPending} onConfirm={() => save.mutate(value)} />
        ) : (
          <button type="submit" className="btn btn-filled" disabled={save.isPending || !dirty} aria-busy={save.isPending}>
            <Icon name="online" />
            {t("common.save")}
          </button>
        )}
      </div>
    </form>
  );
}
