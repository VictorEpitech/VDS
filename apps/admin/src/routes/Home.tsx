import { useQuery } from "@tanstack/react-query";
import type { Health } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Empty, QueryStatus } from "../components/feedback";
import { Icon, type IconName } from "../components/Icon";
import { StatusBadge, type ScreenState } from "../components/StatusBadge";
import { ActiveOverrides } from "../features/overrides/ActiveOverrides";
import { OnboardingCard } from "../features/onboarding/OnboardingCard";
import { useAppUrl } from "../lib/runtime-config";
import { useFleetSummary, useMedia, usePlaylists, useSchedules, useSites } from "../lib/queries";

function Stat({ icon, title, value, to, action, children }: { icon: IconName; title: string; value: number | undefined; to: string; action: string; children?: React.ReactNode }) {
  const id = `stat-${to.replace(/\W+/g, "-")}`;
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id}>
        <Icon name={icon} />
        {title}
      </h2>
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
  const url = useAppUrl();
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
  const sites = useSites();
  const media = useMedia();
  const playlists = usePlaylists();
  const schedules = useSchedules();

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

  return (
    <>
      <div className="page-head">
        <h1>{t("home.title")}</h1>
        <StatusBadge state={server.state} label={server.label} />
      </div>
      <p className="lead">{t("home.intro")}</p>
      <OnboardingCard />
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
      <div className="cards">
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
      <ActiveOverrides />
    </>
  );
}
