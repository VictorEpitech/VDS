import { useQueries } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { powerKeys, useAllAgents, useIncidents, useScreens, useSites, type Agent, type Command, type Screen, type ScreenPower } from "../../lib/queries";
import { powerSetArgs, useRemoteCommand } from "./commands";
import { DetectPanel, reasonOf } from "./DetectPanel";
import { displayState } from "./status";

function ScreenCard({ screen, siteName, state, agent }: { screen: Screen; siteName: string; state: ReturnType<typeof displayState>; agent: Agent | undefined }) {
  const { t, i18n } = useTranslation();
  const cmd = useRemoteCommand();
  const [last, setLast] = useState<{ kind: "on" | "off" | "probe" | "detect"; command: Command } | null>(null);
  const [showDetect, setShowDetect] = useState(false);
  const ready = agent?.status === "online";
  const hintId = `hint-${screen.id}`;
  const run = (kind: "on" | "off" | "probe" | "detect") => {
    if (!agent) return;
    setLast(null);
    setShowDetect(false);
    cmd.mutate(
      { siteId: screen.site_id, agentId: agent.id, screenId: screen.id, name: kind === "probe" ? "power.probe" : kind === "detect" ? "power.detect" : "power.set", args: kind === "on" || kind === "off" ? powerSetArgs(kind) : {} },
      {
        onSuccess: (command) => {
          setLast({ kind, command });
          if (kind === "detect") setShowDetect(true);
        },
      },
    );
  };
  const when = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  const result = last && last.kind !== "detect" ? last : null;
  return (
    <li className="card" aria-labelledby={`sup-${screen.id}`}>
      <h3 id={`sup-${screen.id}`}>
        <Icon name="screen" />
        {screen.name}
      </h3>
      <p className="meta">{siteName}</p>
      <p>
        <StatusBadge state={state} />
      </p>
      {screen.device?.last_seen_at ? <p className="meta">{t("supervision.last_seen", { when: when(screen.device.last_seen_at) })}</p> : null}
      <div className="actions">
        {(["on", "off"] as const).map((s) => (
          <button key={s} type="button" className="btn btn-text" disabled={!ready || cmd.isPending} aria-describedby={ready ? undefined : hintId} onClick={() => run(s)} aria-label={t(`supervision.power.${s}_for`, { name: screen.name })}>
            <Icon name="power" />
            {t(`supervision.power.${s}`)}
          </button>
        ))}
        <button type="button" className="btn btn-text" disabled={!ready || cmd.isPending} aria-describedby={ready ? undefined : hintId} onClick={() => run("detect")} aria-label={t("supervision.detect.run_for", { name: screen.name })}>
          <Icon name="detect" />
          {t("supervision.detect.run")}
        </button>
      </div>
      {!ready ? (
        <p className="meta" id={hintId}>
          {agent ? t("supervision.agent_offline") : t("supervision.no_agent")}
        </p>
      ) : null}
      {cmd.isPending ? (
        <p className="alert alert-info" role="status">
          <Icon name="processing" />
          <span>{t("supervision.command.pending")}</span>
        </p>
      ) : null}
      {cmd.isError ? <ErrorAlert error={cmd.error} /> : null}
      {result ? (
        result.command.status === "ok" ? (
          <p className="alert alert-success" role="status">
            <Icon name="online" />
            <span>{result.kind === "probe" ? t("supervision.command.state", { state: String((result.command.result as { state?: string } | null)?.state ?? "unknown") }) : t(`supervision.command.done_${result.kind}`, { name: screen.name })}</span>
          </p>
        ) : (
          <p className="alert alert-error" role="alert">
            <Icon name="warning" />
            <span>{t("supervision.command.failed", { reason: reasonOf(t, result.command) })}</span>
          </p>
        )
      ) : null}
      {showDetect && last?.kind === "detect" ? <DetectPanel screenId={screen.id} screenName={screen.name} command={last.command} onClose={() => setShowDetect(false)} /> : null}
    </li>
  );
}

export function ScreensStatus() {
  const { t } = useTranslation();
  const api = useApi();
  const screens = useScreens({ refetchInterval: 15_000 });
  const sites = useSites();
  const agents = useAllAgents();
  const incidents = useIncidents("open");
  const active = (screens.data ?? []).filter((s) => !s.archived_at);
  const powers = useQueries({
    queries: active.map((s) => ({ queryKey: powerKeys.screen(s.id), staleTime: 60_000, queryFn: () => api.get<ScreenPower>(`/power/screens/${s.id}`) })),
  });
  const now = new Date();
  return (
    <section className="section" aria-labelledby="park-h">
      <h2 id="park-h">
        <Icon name="screen" />
        {t("supervision.park.title")}
      </h2>
      <p className="lead">{t("supervision.park.intro")}</p>
      <QueryStatus query={screens} />
      {screens.isSuccess && active.length === 0 ? <Empty icon="screen">{t("screens.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {active.map((s, i) => (
          <ScreenCard
            key={s.id}
            screen={s}
            siteName={sites.data?.find((x) => x.id === s.site_id)?.name ?? ""}
            state={displayState(s, powers[i]?.data, incidents.data ?? [], now)}
            agent={(agents.data ?? []).find((a) => a.site_id === s.site_id && a.status !== "revoked")}
          />
        ))}
      </ul>
    </section>
  );
}
