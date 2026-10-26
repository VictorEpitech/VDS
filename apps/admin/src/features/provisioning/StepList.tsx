import { useTranslation } from "react-i18next";
import { Icon, type IconName } from "../../components/Icon";
import { StatusBadge, type ScreenState } from "../../components/StatusBadge";
import { codeKey, STEP_ORDER, type Provision, type StepStatus } from "./hooks";

const BADGE: Record<StepStatus, { state: ScreenState; icon: IconName }> = {
  pending: { state: "scheduled_off", icon: "scheduled_off" },
  running: { state: "pairing", icon: "processing" },
  waiting_user: { state: "overridden", icon: "pairing" },
  ok: { state: "online", icon: "online" },
  skipped: { state: "scheduled_off", icon: "back" },
  failed: { state: "offline", icon: "failed" },
};

/** Message lisible d'un code `provision.*` ; code inconnu : message générique. */
export function useCodeText() {
  const { t, i18n } = useTranslation();
  return (code: string | null): string | null => {
    if (!code) return null;
    const key = `provision.codes.${codeKey(code)}`;
    return i18n.exists(key) ? t(key) : t("provision.codes.unknown");
  };
}

/** Les 11 étapes avec statut (couleur + icône + libellé) et message d'erreur ou d'avertissement. */
export function StepList({ steps }: { steps: Provision["steps"] }) {
  const { t } = useTranslation();
  const codeText = useCodeText();
  const byId = new Map(steps.map((s) => [s.step, s]));
  return (
    <ol className="list-reset step-list" aria-label={t("provision.run.steps_label")}>
      {STEP_ORDER.map((id) => {
        const s = byId.get(id);
        const status: StepStatus = s?.status ?? "pending";
        const { state, icon } = BADGE[status];
        const message = codeText(s?.code ?? null);
        return (
          <li key={id} className="step-row" data-status={status} aria-current={status === "running" || status === "waiting_user" ? "step" : undefined}>
            <span className="step-name">{t(`provision.step.${id}`)}</span>
            <StatusBadge state={state} icon={icon} label={t(`provision.step_status.${status}`)} />
            {message && (status === "failed" || status === "skipped" || status === "waiting_user") ? (
              <p className="step-message meta">
                <Icon name={status === "failed" ? "failed" : "warning"} />
                <span>{message}</span>
              </p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
