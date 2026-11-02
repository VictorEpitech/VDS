import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { Agent, Group, Screen, Site } from "../../lib/queries";
import { useActionQueue } from "./ActionQueueProvider";
import { MAX_SELECTION, type BatchKind, type Target } from "./queue";

interface Props {
  selected: Screen[];
  agents: readonly Agent[];
  groups: readonly Group[];
  sites: readonly Site[];
  onClear: () => void;
}

const onlineAgent = (agents: readonly Agent[], siteId: string) => agents.find((a) => a.site_id === siteId && a.status === "online");

/** Barre d'actions de la sélection : tonale, sans ombre ; feuille basse à 375 px. Admin seulement (le parent la masque sinon). */
export function SelectionBar({ selected, agents, groups, sites, onClear }: Props) {
  const { t } = useTranslation();
  const { queue } = useActionQueue();
  const uid = useId();
  const [mode, setMode] = useState<"group" | "delete" | null>(null);
  const [groupId, setGroupId] = useState("");
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (mode === "delete") confirmRef.current?.focus();
  }, [mode]);

  const target = (s: Screen): Target => ({ screenId: s.id, name: s.name, siteId: s.site_id, agentId: onlineAgent(agents, s.site_id)?.id });
  const powered = selected.filter((s) => onlineAgent(agents, s.site_id));
  const groupTargets = selected.filter((s) => !groupId || groups.find((g) => g.id === groupId)?.site_id === s.site_id);

  const submit = (kind: BatchKind, list: Screen[], extra: { groupId?: string | null } = {}) => {
    if (!list.length) return;
    queue.enqueue(kind, list.map(target), { ...extra, skipped: selected.length - list.length });
    setMode(null);
    onClear();
  };

  return (
    <div className="selection-bar" role="region" aria-label={t("actions.bar.label")}>
      <p className="selection-count" role="status">
        <Icon name="check" />
        {t("actions.bar.selected", { count: selected.length })}
        {selected.length >= MAX_SELECTION ? <span className="meta"> · {t("actions.bar.cap", { max: MAX_SELECTION })}</span> : null}
      </p>
      {powered.length < selected.length ? <p className="meta">{t("actions.bar.no_agent", { count: selected.length - powered.length })}</p> : null}
      <div className="actions">
        {(["on", "off"] as const).map((k) => (
          <button key={k} type="button" className="btn btn-text" disabled={!powered.length} onClick={() => submit(k, powered)}>
            <Icon name="power" />
            {t(`actions.kind.${k}`)}
          </button>
        ))}
        <button type="button" className="btn btn-text" aria-expanded={mode === "group"} onClick={() => setMode(mode === "group" ? null : "group")}>
          <Icon name="site" />
          {t("actions.kind.group")}
        </button>
        <button type="button" className="btn btn-text btn-danger" aria-expanded={mode === "delete"} onClick={() => setMode(mode === "delete" ? null : "delete")}>
          <Icon name="delete" />
          {t("actions.kind.delete")}
        </button>
        <button type="button" className="btn btn-text" onClick={onClear}>
          {t("actions.bar.clear")}
        </button>
      </div>
      {mode === "group" ? (
        <div className="selection-panel">
          <div className="field">
            <label htmlFor={`${uid}-group`}>{t("actions.group.label")}</label>
            <select id={`${uid}-group`} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
              <option value="">{t("screens.create.no_group")}</option>
              {sites.map((site) => {
                const own = groups.filter((g) => g.site_id === site.id);
                return own.length ? (
                  <optgroup key={site.id} label={site.name}>
                    {own.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null;
              })}
            </select>
          </div>
          {groupTargets.length < selected.length ? <p className="meta">{t("actions.group.other_site", { count: selected.length - groupTargets.length })}</p> : null}
          <div className="actions">
            <button type="button" className="btn btn-filled" disabled={!groupTargets.length} onClick={() => submit("group", groupTargets, { groupId: groupId || null })}>
              <Icon name="check" />
              {t("actions.group.apply", { count: groupTargets.length })}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setMode(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
      {mode === "delete" ? (
        <div className="selection-panel" role="alertdialog" aria-label={t("actions.delete.title", { count: selected.length })}>
          <p role="alert">
            <Icon name="warning" /> {t("actions.delete.warning", { count: selected.length })}
          </p>
          <div className="actions">
            <button ref={confirmRef} type="button" className="btn btn-danger-filled" onClick={() => submit("delete", selected)}>
              <Icon name="delete" />
              {t("actions.delete.confirm", { count: selected.length })}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setMode(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
