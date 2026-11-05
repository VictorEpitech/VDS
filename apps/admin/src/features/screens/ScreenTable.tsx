import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import type { Agent, Group, Screen, ScreenPlayback, ScreenPower, Site } from "../../lib/queries";
import type { DisplayState } from "../supervision/status";
import { currentOffSlot } from "../supervision/status";
import { ScreenDeleteForm } from "./ScreenDeleteForm";
import { ScreenEditForm } from "./ScreenEditForm";
import { useRemoteCommand, powerSetArgs } from "../supervision/commands";
import { useQueryClient } from "@tanstack/react-query";

interface ScreenRowData {
  s: Screen;
  power?: ScreenPower;
  state: DisplayState;
}

interface Props {
  screens: readonly ScreenRowData[];
  sites: readonly Site[];
  groups: readonly Group[];
  agents: readonly Agent[];
  playback: readonly ScreenPlayback[] | undefined;
  apk?: unknown;
  isAdmin: boolean;
  canForce?: boolean;
  selection: ReadonlySet<string>;
  selectDisabled: boolean;
  onSelect: (id: string, checked: boolean) => void;
  onSelectAll: (checked: boolean) => void;
  allSelected: boolean;
  onForceScreen?: (id: string) => void;
  onNotice: (message: string | null) => void;
}

export function ScreenTable({
  screens,
  sites,
  groups,
  agents,
  playback,
  isAdmin,
  canForce = false,
  selection,
  selectDisabled,
  onSelect,
  onSelectAll,
  allSelected,
  onForceScreen,
  onNotice,
}: Props) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const cmd = useRemoteCommand();
  const [panel, setPanel] = useState<{ screen: Screen; kind: "edit" | "delete"; archive?: boolean } | null>(null);

  const siteName = (id: string) => sites.find((s) => s.id === id)?.name ?? "";
  const groupName = (id: string | null) => (id ? (groups.find((g) => g.id === id)?.name ?? "") : "");
  const fmt = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));

  const handleTogglePower = (s: Screen, power?: ScreenPower) => {
    const agent = agents.find((a) => a.site_id === s.site_id && a.status === "online");
    if (!agent) return;
    const offSlot = currentOffSlot(power, new Date());
    const isCurrentlyOff = Boolean(offSlot);
    const targetKind = isCurrentlyOff ? "on" : "off";
    cmd.mutate(
      { siteId: s.site_id, agentId: agent.id, screenId: s.id, name: "power.set", args: powerSetArgs(targetKind) },
      {
        onSuccess: () => {
          void qc.invalidateQueries({ queryKey: ["power", "screen"] });
          onNotice(t("screens.commands.sent", { name: s.name, command: targetKind.toUpperCase() }));
        },
      },
    );
  };

  return (
    <>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- table défilante accessible */}
      <div className="table-scroll screen-table-wrapper" role="region" tabIndex={0} aria-label={t("screens.title")}>
        <table className="screen-table">
          <thead>
            <tr>
              {isAdmin ? (
                <th scope="col" className="cell-select">
                  <input
                    type="checkbox"
                    className="select-box"
                    checked={allSelected}
                    aria-label={t("screens.select.all", { count: screens.length })}
                    onChange={(e) => onSelectAll(e.target.checked)}
                  />
                </th>
              ) : null}
              <th scope="col">{t("screens.table.status")}</th>
              <th scope="col">{t("screens.table.screen")}</th>
              <th scope="col">{t("screens.table.location")}</th>
              <th scope="col">{t("screens.table.broadcast")}</th>
              <th scope="col">{t("screens.table.power")}</th>
              <th scope="col" className="cell-actions">
                <span className="visually-hidden">{t("screens.table.actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {screens.map(({ s, power, state }) => {
              const pb = playback?.find((p) => p.screen_id === s.id);
              const offSlot = currentOffSlot(power, new Date());
              const isSelected = selection.has(s.id);
              const powerText = !power
                ? null
                : offSlot
                  ? t("screens.card.power_off_until", { when: fmt(offSlot.to) })
                  : power.plan_id || power.slots.length
                    ? t("screens.card.power_on")
                    : t("screens.card.power_no_plan");

              const broadcastText = pb
                ? pb.source === "none"
                  ? t("screens.card.no_schedule")
                  : [pb.schedule_name, pb.playlist_name, pb.layout_name].filter(Boolean).join(" · ")
                : t("screens.table.none_broadcast");

              return (
                <tr key={s.id} data-selected={isSelected || undefined}>
                  {isAdmin ? (
                    <td className="cell-select">
                      <input
                        type="checkbox"
                        className="select-box"
                        checked={isSelected}
                        disabled={!isSelected && selectDisabled}
                        aria-label={t("screens.table.select", { name: s.name })}
                        onChange={(e) => onSelect(s.id, e.target.checked)}
                      />
                    </td>
                  ) : null}
                  <td>
                    <div className="status-cell">
                      <span className={`screen-live-dot ${state === "online" ? "dot-online" : "dot-offline"}`} />
                      <StatusBadge state={state} />
                    </div>
                  </td>
                  <th scope="row">
                    <div className="screen-name-cell">
                      <strong className="screen-name-title">{s.name}</strong>
                      <span className="badge badge-meta">
                        <Icon name={s.orientation === "portrait" ? "rotate" : "screen"} />
                        {s.orientation === "portrait" ? t("screens.orientation.portrait") : t("screens.orientation.landscape")}
                      </span>
                    </div>
                  </th>
                  <td>
                    <span className="screen-location-text">
                      {siteName(s.site_id)}
                      {s.group_id ? <span className="screen-group-tag"> · {groupName(s.group_id)}</span> : null}
                    </span>
                  </td>
                  <td>
                    <div className="screen-broadcast-cell">
                      {pb ? (
                        <>
                          <Icon name="media" />
                          <span className="broadcast-title">
                            {pb.override_id ? `[${t("overrides.status.active")}] ` : ""}
                            {broadcastText}
                          </span>
                        </>
                      ) : (
                        <span className="meta">{t("screens.table.none_broadcast")}</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="screen-power-cell">
                      <Icon name="power" />
                      {powerText}
                    </span>
                  </td>
                  <td className="cell-actions">
                    <div className="actions">
                      {canForce && onForceScreen ? (
                        <button
                          type="button"
                          className="btn btn-text"
                          title={t("overrides.force")}
                          onClick={() => onForceScreen(s.id)}
                        >
                          <Icon name="overridden" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn btn-text"
                        title={offSlot ? t("supervision.power.on") : t("supervision.power.off")}
                        onClick={() => handleTogglePower(s, power)}
                      >
                        <Icon name="power" />
                      </button>
                      <button
                        type="button"
                        className="btn btn-text"
                        aria-label={`Modifier l'écran ${s.name}`}
                        onClick={() => setPanel({ screen: s, kind: "edit" })}
                      >
                        <Icon name="layout" />
                        <span className="action-label">{t("screens.edit.action")}</span>
                      </button>
                      <button
                        type="button"
                        className="btn btn-text"
                        aria-label={`Supprimer l'écran ${s.name}`}
                        onClick={() => setPanel({ screen: s, kind: "delete" })}
                      >
                        <Icon name="delete" />
                        <span className="action-label">{t("screens.delete.action")}</span>
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {panel?.kind === "edit" ? (
        <ScreenEditForm
          screen={panel.screen}
          onClose={() => setPanel(null)}
          onDone={(name) => {
            setPanel(null);
            onNotice(t("screens.edit.saved", { name }));
          }}
        />
      ) : null}
      {panel?.kind === "delete" ? (
        <ScreenDeleteForm
          screen={panel.screen}
          onArchive={() => setPanel({ screen: panel.screen, kind: "edit", archive: true })}
          onClose={() => setPanel(null)}
          onDone={(name) => {
            setPanel(null);
            onNotice(t("screens.delete.done", { name }));
          }}
        />
      ) : null}
    </>
  );
}
