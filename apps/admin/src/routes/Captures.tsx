import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Empty, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { PresenceBadge } from "../components/StatusBadge";
import { canScreenshot, relativeAge } from "../features/commands/commands";
import { RefreshShot } from "../features/commands/RefreshShot";
import { ScreenshotView } from "../features/commands/ScreenshotView";
import { usePlayback } from "../features/planning/now";
import { displayState, ISSUE_STATES } from "../features/supervision/status";
import { useAllGroups, useIncidents, useScreens, useSites } from "../lib/queries";

/** Tableau de bord des dernières captures du périmètre. Ce n'est pas un mur d'écrans : une image fixe par écran, jamais de flux. */
export function Captures() {
  const { t, i18n } = useTranslation();
  const screens = useScreens({ refetchInterval: 15_000 });
  const sites = useSites();
  const groups = useAllGroups();
  const incidents = useIncidents("open");
  const playback = usePlayback();
  const [siteId, setSiteId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [issues, setIssues] = useState(false);

  const now = new Date();
  const rows = (screens.data ?? [])
    .filter((s) => !s.archived_at)
    .filter((s) => !siteId || s.site_id === siteId)
    .filter((s) => !groupId || s.group_id === groupId)
    .map((s) => ({ s, pb: playback.data?.find((p) => p.screen_id === s.id), state: displayState(s, undefined, incidents.data ?? [], now, playback.data?.find((p) => p.screen_id === s.id)) }))
    .filter((r) => !issues || ISSUE_STATES.has(r.state));
  const siteGroups = (groups.data ?? []).filter((g) => !siteId || g.site_id === siteId);
  const siteName = (id: string) => sites.data?.find((s) => s.id === id)?.name ?? "";

  return (
    <>
      <div className="page-head">
        <h1>{t("screens.captures.title")}</h1>
        <div className="actions">
          <Link className="btn btn-text" to="/screens">
            <Icon name="screen" />
            {t("screens.captures.back")}
          </Link>
        </div>
      </div>
      <p className="lead">{t("screens.captures.intro")}</p>
      <div className="filter-bar" role="group" aria-label={t("screens.captures.filters")}>
        <div className="field">
          <label htmlFor="cap-site">{t("screens.captures.site")}</label>
          <select id="cap-site" value={siteId} onChange={(e) => { setSiteId(e.target.value); setGroupId(""); }}>
            <option value="">{t("screens.captures.all_sites")}</option>
            {(sites.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="cap-group">{t("screens.captures.group")}</label>
          <select id="cap-group" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            <option value="">{t("screens.captures.all_groups")}</option>
            {siteGroups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>
        <button type="button" className="btn btn-text" aria-pressed={issues} onClick={() => setIssues(!issues)}>
          <Icon name="incident" />
          {t("screens.filter.issues")}
        </button>
      </div>
      <QueryStatus query={screens} />
      {screens.isSuccess && rows.length === 0 ? <Empty icon="screen">{t("screens.captures.empty")}</Empty> : null}
      <ul className="cards list-reset" aria-label={t("screens.captures.title")}>
        {rows.map(({ s, pb }) => (
          <li key={s.id} className="card screen-card" aria-labelledby={`cap-${s.id}`}>
            <h2 id={`cap-${s.id}`}>
              <Icon name="screen" />
              {s.name}
            </h2>
            <p className="meta">{siteName(s.site_id)}</p>
            {s.device ? <p><PresenceBadge presence={s.device.presence} /></p> : null}
            <ScreenshotView screen={s} playback={pb} />
            {canScreenshot(s) && s.last_screenshot ? <p className="meta">{t("screens.captures.age", { age: relativeAge(s.last_screenshot.captured_at, i18n.language) })}</p> : null}
            <div className="actions">
              <RefreshShot screen={s} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
