import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { PresenceBadge, StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { keys, type Agent, type Command, type Screen, type ScreenApk, type FleetApk, type ScreenPlayback, type ScreenPower } from "../../lib/queries";
import { Link } from "react-router-dom";
import { ApkUpdateConfirm, needsUpdate, planApkUpdate } from "../actions/apk";
import { useActionQueue } from "../actions/ActionQueueProvider";
import { OverrideControls, OverrideUntil } from "../overrides/OverrideControls";
import type { Override } from "../overrides/overrides";
import { CommandPanel } from "../commands/CommandPanel";
import { KioskControls } from "../kiosk/KioskControls";
import { KioskSetting } from "../kiosk/KioskSetting";
import { ExclusiveSetting } from "../layouts/ExclusiveSetting";
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
  /** Version de l'application installée et éligibilité à la mise à jour (administrateur seulement). */
  apk?: ScreenApk;
  availableApk?: FleetApk["available"];
  /** Agent du site (en ligne de préférence) ; les commandes d'alimentation passent par lui. */
  agent: Agent | undefined;
  isAdmin: boolean;
  /** Administrateur ou éditeur : peut forcer, prolonger et arrêter. */
  canForce?: boolean;
  /** Forçage actif sur cet écran (celui de la programmation résolue). */
  override?: Override;
  /** Tous les écrans (pour annoncer les écrans hors ligne d'un forçage de groupe ou de site). */
  allScreens?: readonly Screen[];
  onForce?: () => void;
  provisionable: boolean;
  selected: boolean;
  selectDisabled: boolean;
  onSelect: (checked: boolean) => void;
  onNotice: (message: string | null) => void;
}

/** Carte écran unique, utilisée sous tous les filtres : état, alimentation, programmation active, actions. */
export function ScreenCard({ screen, siteName, groupName, state, power, playback, apk, availableApk, agent, isAdmin, canForce = false, override, allScreens = [], onForce, provisionable, selected, selectDisabled, onSelect, onNotice }: Props) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const cmd = useRemoteCommand();
  const [last, setLast] = useState<{ kind: Kind; command: Command } | null>(null);
  const { queue } = useActionQueue();
  const [confirmApk, setConfirmApk] = useState(false);
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
  const updatable = isAdmin && needsUpdate(apk);
  const apkPlan = planApkUpdate([screen], apk && availableApk !== undefined ? { available: availableApk, screens: [apk] } : undefined, agent ? [agent] : []);
  const apkVersion = availableApk ? `${availableApk.version_name} (${availableApk.version_code})` : "";
  const apkReason = apkPlan.excluded[0]?.reason;
  const startApk = () => {
    setConfirmApk(false);
    queue.enqueue("apk_update", [{ screenId: screen.id, name: screen.name, siteId: screen.site_id, agentId: agent?.id }]);
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
        {playback.until && playback.source !== "override" ? <span className="meta"> · {t("screens.card.until", { when: fmt(playback.until, { timeStyle: "short", dateStyle: "short" }) })}</span> : null}
      </>
    );

  return (
    <li className="card screen-card" aria-labelledby={`screen-${screen.id}`}>
      <div className="screen-card-head">
        <div className="screen-card-title-group">
          {isAdmin ? (
            <input type="checkbox" className="select-box" checked={selected} disabled={selectDisabled && !selected} aria-label={t("screens.select.one", { name: screen.name })} onChange={(e) => onSelect(e.target.checked)} />
          ) : null}
          <h2 id={`screen-${screen.id}`} className="screen-card-title">
            <Icon name="screen" />
            <span>{screen.name}</span>
          </h2>
        </div>
        <div className="screen-card-badges">
          {screen.device && ["online", "offline", "pairing", "revoked"].includes(state) ? <PresenceBadge presence={screen.device.presence} /> : <StatusBadge state={state} />}
        </div>
      </div>

      <p className="meta screen-card-location">{[siteName, groupName, t(`screens.orientation.${screen.orientation}`)].filter(Boolean).join(" · ")}</p>

      <div className="screen-card-broadcast">
        <dl className="device-facts">
          {schedule ? (
            <div>
              <dt>{t("screens.card.schedule")}</dt> <dd>{schedule}</dd>
            </div>
          ) : null}
          {powerLine ? (
            <div>
              <dt>{t("screens.card.power")}</dt> <dd>{powerLine}</dd>
            </div>
          ) : null}
          {playback?.next_change_at ? (
            <div>
              <dt>{t("screens.card.next_change")}</dt> <dd>{fmt(playback.next_change_at, { timeStyle: "short", dateStyle: "short" })}</dd>
            </div>
          ) : null}
        </dl>
      </div>

      {playback?.source === "override" ? (
        <div className="override-line highlight-override">
          <p>
            <Icon name="overridden" />{" "}
            {override ? <OverrideUntil override={override} /> : playback.until ? t("overrides.until", { when: fmt(playback.until, { timeStyle: "short", dateStyle: "short" }) }) : null}
          </p>
          {override && canForce ? <OverrideControls override={override} screens={allScreens} label={screen.name} isAdmin={isAdmin} onDone={onNotice} /> : null}
        </div>
      ) : null}

      <div className="screen-subcard screen-section-commands">
        <CommandPanel screen={screen} playback={playback} isAdmin={isAdmin} />
      </div>

      <div className="screen-subcard screen-section-kiosk">
        <KioskControls screen={screen} />
        {isAdmin ? <KioskSetting level="screen" id={screen.id} /> : null}
        {isAdmin ? <ExclusiveSetting level="screen" id={screen.id} /> : null}
      </div>

      {screen.device || (isAdmin && apk && apk.state !== "not_android") ? (
        <div className="screen-subcard screen-section-device">
          {screen.device ? (
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
          ) : (
            <p className="meta">{t("screens.device.none")}</p>
          )}
          {isAdmin && apk && apk.state !== "not_android" ? (
            <dl className="device-facts">
              <div>
                <dt>{t("apk.installed")}</dt>{" "}
                <dd className="mono">{apk.installed_version_code != null ? [apk.installed_version_name, `(${apk.installed_version_code})`].filter(Boolean).join(" ") : <span className="meta">{t("apk.unknown_version")}</span>}</dd>
              </div>
            </dl>
          ) : null}
          {isAdmin && apk?.state === "outdated" ? (
            <p>
              <StatusBadge state="pairing" icon="upload" label={t("apk.badge")} />
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="actions screen-card-quick-actions">
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
        {canForce && onForce && !screen.archived_at ? (
          <button type="button" className="btn btn-text" onClick={onForce} aria-label={t("overrides.force_for", { name: screen.name })}>
            <Icon name="overridden" />
            {t("overrides.force")}
          </button>
        ) : null}
        {updatable ? (
          <button type="button" className="btn btn-text" disabled={!!apkReason || confirmApk} aria-describedby={apkReason ? `apk-${screen.id}` : undefined} aria-expanded={confirmApk} onClick={() => setConfirmApk(true)} aria-label={t("apk.action_for", { name: screen.name })}>
            <Icon name="upload" />
            {t("apk.action")}
          </button>
        ) : null}
      </div>

      {updatable && apkReason ? (
        <p className="meta" id={`apk-${screen.id}`}>
          {t(`apk.reason.${apkReason}`, { defaultValue: t("apk.reason.unknown") })}
        </p>
      ) : null}
      {confirmApk && updatable ? <ApkUpdateConfirm plan={apkPlan} version={apkVersion} onConfirm={startApk} onCancel={() => setConfirmApk(false)} /> : null}
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

      <footer className="screen-card-footer">
        <div className="actions footer-links">
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
        </div>
        {isAdmin ? (
          <div className="actions footer-mgmt">
            <button type="button" className="btn btn-text" aria-label={t("screens.edit.action_for", { name: screen.name })} onClick={() => { onNotice(null); setPanel({ kind: "edit" }); }}>
              <Icon name="maintenance" />
              {t("screens.edit.action")}
            </button>
            <button type="button" className="btn btn-text btn-danger" aria-label={t("screens.delete.action_for", { name: screen.name })} onClick={() => { onNotice(null); setPanel({ kind: "delete" }); }}>
              <Icon name="delete" />
              {t("screens.delete.action")}
            </button>
          </div>
        ) : null}
      </footer>
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