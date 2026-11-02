import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { PresenceBadge, StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { keys, type Agent, type Command, type Screen, type ScreenPlayback, type ScreenPower } from "../../lib/queries";
import { Link } from "react-router-dom";
import { KioskControls } from "../kiosk/KioskControls";
import { KioskSetting } from "../kiosk/KioskSetting";
import { powerSetArgs, useRemoteCommand } from "../supervision/commands";
import { DetectPanel, reasonOf } from "../supervision/DetectPanel";
import { currentOffSlot, type DisplayState } from "../supervision/status";
import { ScreenDeleteForm } from "./ScreenDeleteForm";
import { ScreenEditForm } from "./ScreenEditForm";

type Kind = "on" | "off" | "probe" | "detect";

interface Props {
  screen: Screen;
  siteName: string;
  groupName: string;
  state: DisplayState;
  power: ScreenPower | undefined;
  playback: ScreenPlayback | undefined;
  /** Agent du site (en ligne de préférence) ; les commandes d'alimentation passent par lui. */
  agent: Agent | undefined;
  isAdmin: boolean;
  provisionable: boolean;
  selected: boolean;
  selectDisabled: boolean;
  onSelect: (checked: boolean) => void;
  onNotice: (message: string | null) => void;
}

/** Carte écran unique, utilisée sous tous les filtres : état, alimentation, programmation active, actions. */
export function ScreenCard({ screen, siteName, groupName, state, power, playback, agent, isAdmin, provisionable, selected, selectDisabled, onSelect, onNotice }: Props) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const cmd = useRemoteCommand();
  const [last, setLast] = useState<{ kind: Kind; command: Command } | null>(null);
  const [showDetect, setShowDetect] = useState(false);
  const [panel, setPanel] = useState<{ kind: "edit" | "delete"; archive?: boolean } | null>(null);
  const revoke = useMutation({
    mutationFn: () => api.delete(`/screens/${screen.id}/device`),
    onMutate: () => onNotice(null),
    onSuccess: () => {
      onNotice(t("screens.revoked", { name: screen.name }));
      void qc.invalidateQueries({ queryKey: keys.screens });
    },
  });

  const ready = agent?.status === "online";
  const hintId = `hint-${screen.id}`;
  const fmt = (iso: string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) => new Intl.DateTimeFormat(i18n.language, opts).format(new Date(iso));
  const run = (kind: Kind) => {
    if (!agent) return;
    setLast(null);
    setShowDetect(false);
    cmd.mutate(
      { siteId: screen.site_id, agentId: agent.id, screenId: screen.id, name: kind === "probe" ? "power.probe" : kind === "detect" ? "power.detect" : "power.set", args: kind === "on" || kind === "off" ? powerSetArgs(kind) : {} },
      {
        onSuccess: (command) => {
          setLast({ kind, command });
          if (kind === "detect") setShowDetect(true);
          if (kind === "on" || kind === "off") void qc.invalidateQueries({ queryKey: ["power", "screen"] });
        },
      },
    );
  };
  const result = last && last.kind !== "detect" ? last : null;
  const offSlot = currentOffSlot(power, new Date());
  const powerLine = !power ? null : offSlot ? t("screens.card.power_off_until", { when: fmt(offSlot.to) }) : power.plan_id || power.slots.length ? t("screens.card.power_on") : t("screens.card.power_no_plan");
  const schedule =
    !playback ? null : playback.source === "none" ? (
      t("screens.card.no_schedule")
    ) : (
      <>
        {[playback.schedule_name, playback.playlist_name].filter(Boolean).join(" · ")}
        {playback.until ? <span className="meta"> · {t("screens.card.until", { when: fmt(playback.until, { timeStyle: "short", dateStyle: "short" }) })}</span> : null}
      </>
    );

  return (
    <li className="card screen-card" aria-labelledby={`screen-${screen.id}`}>
      <div className="screen-card-head">
        {isAdmin ? (
          <input type="checkbox" className="select-box" checked={selected} disabled={selectDisabled && !selected} aria-label={t("screens.select.one", { name: screen.name })} onChange={(e) => onSelect(e.target.checked)} />
        ) : null}
        <h2 id={`screen-${screen.id}`}>
          <Icon name="screen" />
          {screen.name}
        </h2>
      </div>
      <p className="meta">{[siteName, groupName, t(`screens.orientation.${screen.orientation}`)].filter(Boolean).join(" · ")}</p>
      <p>{screen.device && ["online", "offline", "pairing", "revoked"].includes(state) ? <PresenceBadge presence={screen.device.presence} /> : <StatusBadge state={state} />}</p>
      {screen.device ? (
        <>
          <dl className="device-facts">
            <div>
              <dt>{t("screens.device.platform")}</dt> <dd className="mono">{[screen.device.platform, screen.device.model].filter(Boolean).join(" ")}</dd>
            </div>
            {screen.device.app_version ? (
              <div>
                <dt>{t("screens.device.version")}</dt> <dd className="mono">{screen.device.app_version}</dd>
              </div>
            ) : null}
            <div>
              <dt>{t("screens.device.last_seen")}</dt> <dd>{screen.device.last_seen_at ? fmt(screen.device.last_seen_at) : t("screens.device.never_seen")}</dd>
            </div>
            {screen.device.revoked_at ? (
              <div>
                <dt>{t("screens.device.revoked_at")}</dt> <dd>{fmt(screen.device.revoked_at)}</dd>
              </div>
            ) : null}
          </dl>
        </>
      ) : (
        <p className="meta">{t("screens.device.none")}</p>
      )}
      <dl className="device-facts">
        {powerLine ? (
          <div>
            <dt>{t("screens.card.power")}</dt> <dd>{powerLine}</dd>
          </div>
        ) : null}
        {schedule ? (
          <div>
            <dt>{t("screens.card.schedule")}</dt> <dd>{schedule}</dd>
          </div>
        ) : null}
      </dl>
      <KioskControls screen={screen} />
      {isAdmin ? <KioskSetting level="screen" id={screen.id} /> : null}
      <div className="actions">
        {isAdmin
          ? (["on", "off"] as const).map((s) => (
              <button key={s} type="button" className="btn btn-text" disabled={!ready || cmd.isPending} aria-describedby={ready ? undefined : hintId} onClick={() => run(s)} aria-label={t(`supervision.power.${s}_for`, { name: screen.name })}>
                <Icon name="power" />
                {t(`supervision.power.${s}`)}
              </button>
            ))
          : null}
        {isAdmin ? (
          <button type="button" className="btn btn-text" disabled={!ready || cmd.isPending} aria-describedby={ready ? undefined : hintId} onClick={() => run("detect")} aria-label={t("supervision.detect.run_for", { name: screen.name })}>
            <Icon name="detect" />
            {t("supervision.detect.run")}
          </button>
        ) : null}
      </div>
      {isAdmin && !ready ? (
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
          <SuccessAlert>{result.kind === "probe" ? t("supervision.command.state", { state: String((result.command.result as { state?: string } | null)?.state ?? "unknown") }) : t(`supervision.command.done_${result.kind}`, { name: screen.name })}</SuccessAlert>
        ) : (
          <p className="alert alert-error" role="alert">
            <Icon name="warning" />
            <span>{t("supervision.command.failed", { reason: reasonOf(t, result.command) })}</span>
          </p>
        )
      ) : null}
      {showDetect && last?.kind === "detect" ? <DetectPanel screenId={screen.id} screenName={screen.name} command={last.command} onClose={() => setShowDetect(false)} /> : null}
      <div className="actions">
          <Link className="btn btn-text" to={`/screens/add?screen=${screen.id}`}>
            <Icon name="pairing" />
            {t("screens.pair_this")}
          </Link>
          {isAdmin && provisionable ? (
            <Link className="btn btn-text" to={`/screens/add/android?screen=${screen.id}`} aria-label={t("screens.reinstall_for", { name: screen.name })}>
              <Icon name="detect" />
              {t("screens.reinstall")}
            </Link>
          ) : null}
          {screen.device && !screen.device.revoked_at && screen.device.presence !== "revoked" ? (
            <ConfirmAction icon="revoke" label={t("screens.revoke.action")} confirmLabel={t("screens.revoke.confirm", { name: screen.name })} pending={revoke.isPending} onConfirm={() => revoke.mutate()} />
          ) : null}
          {isAdmin ? (
            <>
              <button type="button" className="btn btn-text" aria-label={t("screens.edit.action_for", { name: screen.name })} onClick={() => { onNotice(null); setPanel({ kind: "edit" }); }}>
                <Icon name="maintenance" />
                {t("screens.edit.action")}
              </button>
              <button type="button" className="btn btn-text btn-danger" aria-label={t("screens.delete.action_for", { name: screen.name })} onClick={() => { onNotice(null); setPanel({ kind: "delete" }); }}>
                <Icon name="delete" />
                {t("screens.delete.action")}
              </button>
            </>
          ) : null}
      </div>
      {revoke.isError ? <ErrorAlert error={revoke.error} /> : null}
      {isAdmin && panel?.kind === "edit" ? (
        <ScreenEditForm screen={screen} initialArchived={panel.archive} onClose={() => setPanel(null)} onDone={(m) => { setPanel(null); onNotice(m); }} />
      ) : null}
      {isAdmin && panel?.kind === "delete" ? (
        <ScreenDeleteForm screen={screen} onClose={() => setPanel(null)} onDone={(m) => { setPanel(null); onNotice(m); }} onArchive={() => setPanel({ kind: "edit", archive: true })} />
      ) : null}
    </li>
  );
}
