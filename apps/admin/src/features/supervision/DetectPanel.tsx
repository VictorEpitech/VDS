import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { configKey, type Command } from "../../lib/queries";

interface Candidate {
  driver: string;
  confidence: number;
  proposed_config: Record<string, unknown>;
  requires_user_action: string | null;
}

export function candidatesOf(cmd: Command): Candidate[] {
  const raw = (cmd.result as { results?: unknown } | null)?.results;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null && typeof (r as { driver?: unknown }).driver === "string")
    .map((r) => ({
      driver: String(r.driver),
      confidence: typeof r.confidence === "number" ? r.confidence : 0,
      proposed_config: (r.proposed_config as Record<string, unknown> | undefined) ?? {},
      requires_user_action: typeof r.requires_user_action === "string" ? r.requires_user_action : null,
    }))
    .sort((a, b) => b.confidence - a.confidence);
}

/** Assistant de détection : propose un pilote par écran ; la configuration n'est enregistrée qu'après confirmation. */
export function DetectPanel({ screenId, screenName, command, onClose }: { screenId: string; screenName: string; command: Command; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const [applied, setApplied] = useState<string | null>(null);
  const found = candidatesOf(command);
  const apply = useMutation({
    mutationFn: async (c: Candidate) => {
      for (const [key, value] of Object.entries(c.proposed_config)) await api.put(`/config/screen/${screenId}/${c.driver}/${key}`, { value });
      return c.driver;
    },
    onSuccess: async (driver) => {
      setApplied(driver);
      await qc.invalidateQueries({ queryKey: configKey("screen", screenId, driver) });
    },
  });
  const pct = new Intl.NumberFormat(i18n.language, { style: "percent", maximumFractionDigits: 0 });
  return (
    <section className="card-inner" aria-label={t("supervision.detect.title", { name: screenName })}>
      <h4>{t("supervision.detect.title", { name: screenName })}</h4>
      {command.status !== "ok" ? (
        <p className="alert alert-error" role="alert">
          <Icon name="warning" />
          <span>{t("supervision.command.failed", { reason: reasonOf(t, command) })}</span>
        </p>
      ) : found.length === 0 ? (
        <p className="meta" role="status">
          {t("supervision.detect.none")}
        </p>
      ) : (
        <ul className="list-reset rows">
          {found.map((c) => (
            <li key={c.driver} className="row-line">
              <span>
                <code>{c.driver}</code> · {t("supervision.detect.confidence", { value: pct.format(c.confidence) })}
                {c.requires_user_action ? <span className="meta"> · {t(c.requires_user_action, { defaultValue: t("supervision.detect.user_action") })}</span> : null}
              </span>
              <button type="button" className="btn btn-text" disabled={apply.isPending || applied !== null} onClick={() => apply.mutate(c)} aria-label={t("supervision.detect.use_for", { driver: c.driver })}>
                <Icon name="check" />
                {t("supervision.detect.use")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {apply.isError ? <ErrorAlert error={apply.error} /> : null}
      {applied ? <SuccessAlert>{t("supervision.detect.applied", { driver: applied })}</SuccessAlert> : null}
      <div className="actions">
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.dismiss")}
        </button>
      </div>
    </section>
  );
}

export function reasonOf(t: (k: string, o?: Record<string, unknown>) => string, c: Command): string {
  if (c.status === "expired") return t("supervision.command.reasons.expired");
  if (c.status === "rejected") return t("supervision.command.reasons.rejected");
  return c.code ? t(`supervision.command.codes.${c.code}`, { defaultValue: t("supervision.command.reasons.unknown") }) : t("supervision.command.reasons.unknown");
}
