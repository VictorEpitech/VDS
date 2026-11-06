import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { Agent, FleetApk, Screen, Site } from "../../lib/queries";
import { ApkUpdateConfirm, needsUpdate, planApkUpdate } from "./apk";
import { useActionQueue } from "./ActionQueueProvider";
import type { Target } from "./queue";

interface Props {
  apk: FleetApk;
  screens: readonly Screen[];
  sites: readonly Site[];
  agents: readonly Agent[];
}

/** Version disponible, aide si elle manque, et mise à jour d'un site entier (administrateur seulement, le parent le garantit). */
export function ApkPanel({ apk, screens, sites, agents }: Props) {
  const { t } = useTranslation();
  const { queue } = useActionQueue();
  const [siteId, setSiteId] = useState<string | null>(null);
  const known = new Map(apk.screens.map((x) => [x.screen_id, x]));
  const androidScreens = screens.filter((s) => known.get(s.id) && known.get(s.id)!.state !== "not_android");
  if (!androidScreens.length) return null;

  const rows = sites
    .map((site) => {
      const own = androidScreens.filter((s) => s.site_id === site.id && needsUpdate(known.get(s.id)));
      return { site, own, plan: planApkUpdate(own, apk, agents) };
    })
    .filter((r) => r.own.length);
  const open = rows.find((r) => r.site.id === siteId);
  const version = apk.available ? `${apk.available.version_name} (${apk.available.version_code})` : "";

  // Ne pas encombrer l'écran avec un panneau technique si tout est déjà à jour
  if (apk.available && !rows.length) return null;

  const start = () => {
    if (!open) return;
    const targets: Target[] = open.plan.eligible.map((s) => ({ screenId: s.id, name: s.name, siteId: s.site_id, agentId: agents.find((a) => a.site_id === s.site_id && a.status === "online")?.id }));
    queue.enqueue("apk_update", targets, { skipped: open.plan.excluded.length });
    setSiteId(null);
  };

  return (
    <section className="card apk-panel" aria-labelledby="apk-panel-title">
      <h2 id="apk-panel-title">
        <Icon name="upload" />
        {t("apk.panel.title")}
      </h2>
      {apk.available ? (
        <p>
          {t("apk.panel.available")} <span className="mono">{version}</span> <span className="meta">· {t(`apk.source.${apk.available.source}`)}</span>
        </p>
      ) : (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("apk.panel.none")}</span>
        </p>
      )}
      {apk.available && !rows.length ? <p className="meta">{t("apk.panel.all_current")}</p> : null}
      <ul className="list-reset rows">
        {rows.map(({ site, own }) => (
          <li key={site.id} className="row-line">
            <span>{t("apk.panel.site_line", { site: site.name, count: own.length })}</span>
            <button type="button" className="btn btn-text" disabled={!apk.available} aria-expanded={siteId === site.id} onClick={() => setSiteId(siteId === site.id ? null : site.id)} aria-label={t("apk.site_action_for", { site: site.name })}>
              <Icon name="upload" />
              {t("apk.site_action")}
            </button>
          </li>
        ))}
      </ul>
      {open && apk.available ? <ApkUpdateConfirm plan={open.plan} version={version} onConfirm={start} onCancel={() => setSiteId(null)} /> : null}
    </section>
  );
}
