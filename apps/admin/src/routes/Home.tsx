import { useQuery } from "@tanstack/react-query";
import type { Health } from "@vds/sdk-ts";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Empty, QueryStatus, SuccessAlert } from "../components/feedback";
import { Icon, type IconName } from "../components/Icon";
import { StatusBadge, type ScreenState } from "../components/StatusBadge";
import { ActiveOverrides } from "../features/overrides/ActiveOverrides";
import { OnboardingCard } from "../features/onboarding/OnboardingCard";
import { NextChanges } from "../features/planning/NextChanges";
import { usePlayback } from "../features/planning/now";
import { QuickPublishModal } from "../features/publication/QuickPublishModal";
import { useAppUrl } from "../lib/runtime-config";
import { useFleetSummary, useMedia, usePlaylists, useSchedules, useScreens, useSites } from "../lib/queries";

function Stat({ icon, title, value, to, action, children }: { icon: IconName; title: string; value: number | undefined; to: string; action: string; children?: React.ReactNode }) {
  const id = `stat-${to.replace(/\W+/g, "-")}`;
  return (
    <section className="card cockpit-pod" aria-labelledby={id}>
      <div className="cockpit-pod-head">
        <h2 id={id}>
          <Icon name={icon} />
          <span>{title}</span>
        </h2>
      </div>
      <p className="stat">{value ?? "–"}</p>
      {children}
      <div className="actions">
        <Link className="btn btn-text" to={to}>
          {action}
        </Link>
      </div>
    </section>
  );
}

export function Home() {
  const { t } = useTranslation();
  const uid = useId();
  const url = useAppUrl();
  const [publishOpen, setPublishOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch(url("/health"), { credentials: "same-origin" });
      if (!res.ok) throw new Error("errors.server_unreachable");
      const body = (await res.json()) as Health;
      if (body.status !== "ok") throw new Error("errors.server_unreachable");
      return true;
    },
    // État réel du serveur : pas de nouvelle tentative silencieuse, rafraîchi régulièrement.
    retry: false,
    refetchInterval: 30_000,
  });
  const summary = useFleetSummary({ refetchInterval: 30_000 });
  const screens = useScreens();
  const sites = useSites();
  const media = useMedia();
  const playlists = usePlaylists();
  const schedules = useSchedules();

  const playback = usePlayback();
  const playbackList = playback.data ?? [];

  const server: { state: ScreenState; label: string } = health.isPending
    ? { state: "pairing", label: t("home.server.checking") }
    : health.isError
      ? { state: "offline", label: t("home.server.offline") }
      : { state: "online", label: t("home.server.online") };

  const total = summary.data?.screens.total ?? 0;
  const online = summary.data?.screens.by_status["online"] ?? 0;
  const waiting = summary.data?.screens.by_status["unseen"] ?? 0;
  const toHandle = summary.data?.to_handle.screens ?? 0;
  const noScreens = summary.isSuccess && total === 0;

  const visibleScreens = useMemo(() => (screens.data ?? []).filter((s) => !s.archived_at), [screens.data]);

  return (
    <>
      <div className="page-head">
        <div className="page-head-title-wrap">
          <h1>{t("home.title")}</h1>
          <StatusBadge state={server.state} label={server.label} />
        </div>
        <div className="actions">
          <button type="button" className="btn btn-filled" onClick={() => setPublishOpen(true)}>
            <Icon name="schedule" />
            {t("quick_publish.cta_button")}
          </button>
        </div>
      </div>
      <p className="lead">{t("home.intro")}</p>
      {notice ? <SuccessAlert>{notice}</SuccessAlert> : null}
      <OnboardingCard />

      {/* Cockpit du Matin : Bannière d'accueil et statut du parc */}
      <div className={`cockpit-hero${toHandle > 0 ? " has-issues" : ""}`}>
        <div className="cockpit-hero-content">
          <div className="cockpit-hero-badge">
            <Icon name={toHandle > 0 ? "warning" : "check"} />
            <span>{toHandle > 0 ? t("home.cockpit.status_issues", { count: toHandle }) : t("home.cockpit.status_ok")}</span>
          </div>
          <h2 className="cockpit-hero-title">
            {toHandle > 0 ? t("home.cockpit.status_issues", { count: toHandle }) : t("home.cockpit.status_ok")}
          </h2>
          <p className="cockpit-hero-sub">
            {toHandle > 0 ? t("home.cockpit.sub_issues") : t("home.cockpit.sub_ok")}
          </p>
        </div>
        <div className="cockpit-hero-actions">
          <button type="button" className="btn btn-filled cockpit-cta-btn" onClick={() => setPublishOpen(true)}>
            <Icon name="schedule" />
            <span>{t("quick_publish.cta_button")}</span>
          </button>
        </div>
      </div>

      <QueryStatus query={summary} />
      {noScreens ? (
        <Empty
          icon="screen"
          action={
            <Link className="btn btn-filled" to="/screens/add">
              <Icon name="add" />
              {t("screens.pair")}
            </Link>
          }
        >
          {t("home.empty")}
        </Empty>
      ) : null}

      <div className="cards cockpit-pods-grid">
        <Stat icon="screen" title={t("home.stats.screens")} value={summary.isSuccess ? total : undefined} to="/screens" action={t("home.stats.screens_action")}>
          {summary.isSuccess && total > 0 ? (
            <ul className="badge-list">
              <li>
                <StatusBadge state="online" label={t("home.stats.online", { count: online })} />
              </li>
              {waiting > 0 ? (
                <li>
                  <StatusBadge state="pairing" label={t("home.stats.waiting", { count: waiting })} />
                </li>
              ) : null}
            </ul>
          ) : null}
        </Stat>
        <Stat icon="incident" title={t("home.stats.to_handle")} value={summary.isSuccess ? toHandle : undefined} to="/screens?filter=issues" action={t("home.stats.to_handle_action")}>
          {summary.isSuccess ? (
            toHandle > 0 ? (
              <StatusBadge state="offline" label={t("home.stats.to_handle_some", { count: toHandle })} />
            ) : (
              <StatusBadge state="online" icon="check" label={t("home.stats.to_handle_none")} />
            )
          ) : null}
        </Stat>
        <Stat icon="media" title={t("home.stats.content")} value={media.isSuccess ? media.data.total : undefined} to="/content/media" action={t("home.stats.content_action")}>
          <p className="meta">{t("home.stats.playlists", { count: playlists.data?.total ?? 0 })}</p>
        </Stat>
        <Stat icon="schedule" title={t("home.stats.playback")} value={schedules.isSuccess ? schedules.data.length : undefined} to="/playback" action={t("home.stats.playback_action")}>
          <p className="meta">{t("home.stats.sites", { count: sites.data?.length ?? 0 })}</p>
        </Stat>
      </div>

      {/* Aperçu en direct du parc (Live Screen Peeker) */}
      {visibleScreens.length > 0 ? (
        <section className="section cockpit-fleet-section" aria-labelledby={`${uid}-fleet`}>
          <div className="section-head">
            <div>
              <h2 id={`${uid}-fleet`} className="cockpit-section-title">
                <Icon name="screen" />
                <span>{t("home.cockpit.fleet_title")}</span>
              </h2>
              <p className="meta">{t("home.cockpit.fleet_sub")}</p>
            </div>
            <Link to="/screens" className="btn btn-text">
              {t("home.stats.screens_action")}
            </Link>
          </div>
          <div className="cockpit-fleet-grid">
            {visibleScreens.slice(0, 4).map((s) => {
              const currentPlayback = playbackList.find((p) => p.screen_id === s.id);
              const currentItem =
                currentPlayback?.playlist_name ??
                (s.device?.presence === "online" ? t("screens.card.no_schedule") : t("screens.device.presence.offline"));
              return (
                <div key={s.id} className="card cockpit-screen-card">
                  <div className="cockpit-screen-preview">
                    <div className="cockpit-tv-frame" data-orientation={s.orientation}>
                      <div className="cockpit-tv-screen">
                        <span className={`cockpit-status-dot dot-${s.device?.presence ?? "offline"}`} />
                        <span className="cockpit-tv-content-title">{currentItem}</span>
                      </div>
                    </div>
                  </div>
                  <div className="cockpit-screen-info">
                    <strong className="cockpit-screen-name">{s.name}</strong>
                    <span className="cockpit-screen-playing">{currentItem}</span>
                  </div>
                  <Link to="/screens" className="btn btn-tonal btn-sm">
                    {t("home.cockpit.view_screen")}
                  </Link>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}

      <NextChanges />
      <ActiveOverrides />

      <QuickPublishModal
        isOpen={publishOpen}
        onClose={() => setPublishOpen(false)}
        onSuccess={(m) => setNotice(m)}
      />
    </>
  );
}
