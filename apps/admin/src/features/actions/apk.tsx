import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { Agent, FleetApk, Screen, ScreenApk } from "../../lib/queries";

export interface ApkPlan {
  eligible: Screen[];
  excluded: { screen: Screen; reason: string }[];
}

const onlineAgent = (agents: readonly Agent[], siteId: string) => agents.find((a) => a.site_id === siteId && a.status === "online");

/** Une mise à jour n'a de sens que pour un appareil Android dont la version n'est pas déjà la dernière. */
export const needsUpdate = (a: ScreenApk | undefined) => !!a && (a.state === "outdated" || a.state === "unknown");

/** Sépare les écrans éligibles (réponse du serveur + agent du site en ligne) de ceux exclus avec leur raison. */
export function planApkUpdate(screens: readonly Screen[], apk: FleetApk | undefined, agents: readonly Agent[]): ApkPlan {
  const eligible: Screen[] = [];
  const excluded: ApkPlan["excluded"] = [];
  for (const screen of screens) {
    const row = apk?.screens.find((x) => x.screen_id === screen.id);
    if (!apk?.available) excluded.push({ screen, reason: "apk_unavailable" });
    else if (!row) excluded.push({ screen, reason: "not_android" });
    else if (!row.updatable) excluded.push({ screen, reason: row.reason ?? "not_android" });
    else if (!onlineAgent(agents, screen.site_id)) excluded.push({ screen, reason: "agent_offline" });
    else eligible.push(screen);
  }
  return { eligible, excluded };
}

interface ConfirmProps {
  plan: ApkPlan;
  version: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Confirmation explicite : l'affichage est coupé 30 à 90 s, l'action n'est pas annulable une fois lancée. */
export function ApkUpdateConfirm({ plan, version, onConfirm, onCancel }: ConfirmProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const count = plan.eligible.length;
  return (
    <div className="selection-panel" role="alertdialog" aria-label={t("apk.confirm.title", { count })}>
      <p role="alert">
        <Icon name="warning" /> {t("apk.confirm.warning", { count, version })}
      </p>
      {plan.excluded.length ? (
        <>
          <p className="meta">{t("apk.confirm.excluded", { count: plan.excluded.length })}</p>
          <ul className="list-reset meta">
            {plan.excluded.map((x) => (
              <li key={x.screen.id}>{t("actions.failure_line", { name: x.screen.name, reason: t(`apk.reason.${x.reason}`, { defaultValue: t("apk.reason.unknown") }) })}</li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="actions">
        <button ref={ref} type="button" className="btn btn-filled" disabled={!count} onClick={onConfirm}>
          <Icon name="upload" />
          {t("apk.confirm.action", { count })}
        </button>
        <button type="button" className="btn btn-text" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}
