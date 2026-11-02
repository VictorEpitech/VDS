import { useEffect, useRef } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "../../../components/Icon";
import { StatusBadge, type ScreenState } from "../../../components/StatusBadge";
import { useApi } from "../../../lib/api";
import { configKey, useAgents, useConfig, type ConfigOut, type Screen } from "../../../lib/queries";
import { useRemoteCommand } from "../../supervision/commands";

export const VERIFY_POLL_MS = 3000;
/** Au-delà (≈ 2 min sans signe de vie), on explique quoi vérifier au lieu d'attendre en silence. */
const STALL_POLLS = 40;
/** Pilotes exécutés par le player : le test d'allumage de l'assistant ne passe que par l'agent de site. */
const PLAYER_DRIVERS = new Set(["power.blank", "power.cec"]);

type Phase = "waiting" | "stale" | "assets" | "ready";

export function verifyPhase(screen: Screen | undefined): Phase {
  const d = screen?.device;
  if (!d || d.presence !== "online" || !d.heartbeat) return "waiting";
  if (d.heartbeat.stale) return "stale";
  if (d.heartbeat.missing_assets > 0) return "assets";
  return "ready";
}

const PHASE_UI: Record<Phase, { state: ScreenState; icon: IconName }> = {
  waiting: { state: "pairing", icon: "processing" },
  stale: { state: "pairing", icon: "processing" },
  assets: { state: "pairing", icon: "processing" },
  ready: { state: "online", icon: "online" },
};

const hasAddress = (cfg: ConfigOut | undefined) =>
  ["host", "mac"].some((k) => {
    const v = cfg?.values[k]?.value;
    return typeof v === "string" && v !== "";
  });

/** Test d'allumage possible : un pilote d'agent est réglé pour l'écran ET un agent du site est en ligne. */
function usePowerTest(screen: Screen) {
  const api = useApi();
  const core = useConfig("screen", screen.id, "core");
  const cascade = core.data?.values["power.cascade"]?.value;
  const drivers = (Array.isArray(cascade) ? cascade : []).filter((x): x is string => typeof x === "string" && !PLAYER_DRIVERS.has(x));
  const configs = useQueries({
    queries: drivers.map((ns) => ({ queryKey: configKey("screen", screen.id, ns), queryFn: () => api.get<ConfigOut>(`/config/screen/${screen.id}/${ns}`) })),
  });
  const agents = useAgents(screen.site_id);
  const agent = agents.data?.find((a) => a.status === "online");
  return { agent, testable: !!agent && configs.some((c) => hasAddress(c.data)) };
}

function PowerTest({ screen }: { screen: Screen }) {
  const { t } = useTranslation();
  const { agent, testable } = usePowerTest(screen);
  const probe = useRemoteCommand();
  if (!testable || !agent) return null;
  const ok = probe.data?.status === "ok";
  const failed = probe.isError || (probe.isSuccess && !ok);
  return (
    <section className="card-inner" aria-labelledby="add-power-title">
      <h3 id="add-power-title">{t("add.verify.power.title")}</h3>
      <p className="field-help">{t("add.verify.power.help")}</p>
      <div className="actions">
        <button
          type="button"
          className="btn btn-tonal"
          disabled={probe.isPending}
          aria-busy={probe.isPending}
          onClick={() => probe.mutate({ siteId: screen.site_id, agentId: agent.id, screenId: screen.id, name: "power.probe" })}
        >
          <Icon name="power" />
          {t("add.verify.power.test")}
        </button>
        <Link className="btn btn-text" to="/power">
          {t("add.verify.power.settings")}
        </Link>
      </div>
      <div role="status" aria-live="polite">
        {probe.isPending ? <StatusBadge state="pairing" icon="processing" label={t("add.verify.power.running")} /> : null}
        {ok ? <StatusBadge state="online" label={t("add.verify.power.ok")} /> : null}
        {failed ? (
          <p className="alert alert-warning">
            <Icon name="warning" />
            <span>{t("add.verify.power.failed")}</span>
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** Étape 4 : sondage 3 s de l'écran jusqu'à « en ligne, battement frais, aucun média manquant », puis test d'allumage facultatif. */
export function VerifyStep({ screen, configFailed, prefilled, replayed, onAnother }: { screen: Screen; configFailed: boolean; prefilled: boolean; replayed: boolean; onAnother: () => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const q = useQuery({
    queryKey: ["screen", screen.id],
    queryFn: () => api.get<Screen>(`/screens/${screen.id}`),
    refetchInterval: (query) => (verifyPhase(query.state.data) === "ready" ? false : VERIFY_POLL_MS),
  });
  const polls = useRef(0);
  useEffect(() => {
    polls.current += 1;
  }, [q.dataUpdatedAt]);
  const phase = verifyPhase(q.data);
  const ui = PHASE_UI[phase];
  const stalled = phase === "waiting" && polls.current >= STALL_POLLS;
  const missing = q.data?.device?.heartbeat?.missing_assets ?? 0;
  return (
    <section className="card form-narrow" aria-labelledby="add-verify-title">
      <h2 id="add-verify-title">{t("add.verify.title", { name: screen.name })}</h2>
      <div role="status" aria-live="polite" className="verify-state">
        <StatusBadge state={ui.state} icon={ui.icon} label={t(`add.verify.phase.${phase}.label`)} />
        <p>{t(`add.verify.phase.${phase}.text`, { count: missing })}</p>
      </div>
      {replayed ? (
        <p className="meta">
          <Icon name="inherit" /> {t("add.verify.replayed")}
        </p>
      ) : null}
      {prefilled && !configFailed ? <p className="meta">{t("discovery.prefilled", { name: screen.name })}</p> : null}
      {configFailed ? (
        <p className="alert alert-warning" role="alert">
          <Icon name="warning" />
          <span>
            {t("discovery.config_failed", { name: screen.name })} <Link to="/power">{t("discovery.config_open")}</Link>
          </span>
        </p>
      ) : null}
      {stalled ? (
        <p className="alert alert-warning" role="alert">
          <Icon name="warning" />
          <span>{t("add.verify.stalled")}</span>
        </p>
      ) : null}
      {phase === "ready" ? <PowerTest screen={screen} /> : null}
      <div className="actions">
        {phase === "ready" ? (
          <Link className="btn btn-filled" to="/playback">
            <Icon name="schedule" />
            {t("add.verify.schedule")}
          </Link>
        ) : null}
        <Link className={phase === "ready" ? "btn btn-text" : "btn btn-filled"} to="/screens">
          <Icon name="screen" />
          {t("add.verify.done")}
        </Link>
        <button type="button" className="btn btn-text" onClick={onAnother}>
          <Icon name="add" />
          {t("add.verify.another")}
        </button>
      </div>
    </section>
  );
}
