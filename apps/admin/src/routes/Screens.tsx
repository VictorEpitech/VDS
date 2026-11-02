import { useQueries } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { useSession } from "../auth/session";
import { Empty, QueryStatus, SuccessAlert } from "../components/feedback";
import { Icon } from "../components/Icon";
import { MAX_SELECTION } from "../features/actions/queue";
import { ApkPanel } from "../features/actions/ApkPanel";
import { SelectionBar } from "../features/actions/SelectionBar";
import { useProvisioningAvailable } from "../features/provisioning/hooks";
import { OverrideForm, type OverrideInitial } from "../features/overrides/OverrideForm";
import { canForce, useOverrides } from "../features/overrides/overrides";
import { ScreenCard } from "../features/screens/ScreenCard";
import { IncidentsList } from "../features/supervision/IncidentsList";
import { displayState, ISSUE_STATES } from "../features/supervision/status";
import { useApi } from "../lib/api";
import { powerKeys, useAllAgents, useAllGroups, useFleetApk, useIncidents, usePlayback, useScreens, useSites, type Agent, type ScreenPower } from "../lib/queries";

/** Agent qui pilote l'alimentation du site : un agent en ligne de préférence, sinon un agent non révoqué. */
function agentOf(agents: readonly Agent[], siteId: string): Agent | undefined {
  const own = agents.filter((a) => a.site_id === siteId && a.status !== "revoked");
  return own.find((a) => a.status === "online") ?? own[0];
}

export function Screens() {
  const { t } = useTranslation();
  const api = useApi();
  const screens = useScreens({ refetchInterval: 15_000 });
  const sites = useSites();
  const groups = useAllGroups();
  const agents = useAllAgents();
  const incidents = useIncidents("open");
  const playback = usePlayback();
  const session = useSession();
  const isAdmin = session.data?.role === "admin";
  const forcer = canForce(session.data?.role);
  const overrides = useOverrides(forcer);
  const [force, setForce] = useState<OverrideInitial | undefined>(undefined);
  const fleetApk = useFleetApk(isAdmin);
  const apk = fleetApk.data;
  const provisionable = useProvisioningAvailable((sites.data ?? []).map((x) => x.id));
  const [params, setParams] = useSearchParams();
  const issues = params.get("filter") === "issues";
  const setFilter = (f: "all" | "issues") => setParams(f === "issues" ? { filter: "issues" } : {}, { replace: true });
  const [notice, setNotice] = useState<string | null>(null);
  const [selection, setSelection] = useState<ReadonlySet<string>>(new Set());

  const active = (screens.data ?? []).filter((s) => !s.archived_at);
  const powers = useQueries({
    queries: active.map((s) => ({ queryKey: powerKeys.screen(s.id), staleTime: 60_000, queryFn: () => api.get<ScreenPower>(`/power/screens/${s.id}`) })),
  });
  const now = new Date();
  const rows = active.map((s, i) => ({ s, power: powers[i]?.data, state: displayState(s, powers[i]?.data, incidents.data ?? [], now, playback.data?.find((p) => p.screen_id === s.id)) }));
  const visible = issues ? rows.filter((r) => ISSUE_STATES.has(r.state)) : rows;
  const chosen = visible.filter((r) => selection.has(r.s.id)).map((r) => r.s);
  const allChosen = visible.length > 0 && visible.slice(0, MAX_SELECTION).every((r) => selection.has(r.s.id));
  const siteName = (id: string) => sites.data?.find((s) => s.id === id)?.name ?? "";
  const groupName = (id: string | null) => (id ? (groups.data?.find((g) => g.id === id)?.name ?? "") : "");
  const toggle = (id: string, on: boolean) =>
    setSelection((cur) => {
      const next = new Set(cur);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const toggleAll = (on: boolean) => setSelection(on ? new Set(visible.slice(0, MAX_SELECTION).map((r) => r.s.id)) : new Set());

  return (
    <>
      <div className="page-head">
        <h1>{t("screens.title")}</h1>
        <div className="actions">
          {forcer ? (
            <button type="button" className="btn btn-text" aria-expanded={force !== undefined} onClick={() => setForce(null)}>
              <Icon name="overridden" />
              {t("overrides.force")}
            </button>
          ) : null}
          <Link className="btn btn-filled" to="/screens/add">
            <Icon name="add" />
            {t("screens.pair")}
          </Link>
        </div>
      </div>
      <p className="lead">{t("screens.intro")}</p>
      {forcer && force !== undefined ? (
        <OverrideForm
          screens={screens.data ?? []}
          groups={groups.data ?? []}
          sites={sites.data ?? []}
          initial={force}
          isAdmin={isAdmin}
          onClose={() => setForce(undefined)}
          onDone={(m) => {
            setForce(undefined);
            setSelection(new Set());
            setNotice(m);
          }}
        />
      ) : null}
      <div className="filter-bar" role="group" aria-label={t("screens.filter.label")}>
        <button type="button" className="btn btn-text" aria-pressed={!issues} onClick={() => setFilter("all")}>
          {t("screens.filter.all")}
        </button>
        <button type="button" className="btn btn-text" aria-pressed={issues} onClick={() => setFilter("issues")}>
          <Icon name="incident" />
          {t("screens.filter.issues")}
        </button>
      </div>
      {issues ? <IncidentsList /> : null}
      <QueryStatus query={screens} />
      {isAdmin && apk ? <ApkPanel apk={apk} screens={active} sites={sites.data ?? []} agents={agents.data ?? []} /> : null}
      {notice ? <SuccessAlert>{notice}</SuccessAlert> : null}
      {!issues && screens.isSuccess && active.length === 0 ? (
        <Empty
          icon="screen"
          action={
            <Link className="btn btn-filled" to="/screens/add">
              <Icon name="add" />
              {t("screens.pair")}
            </Link>
          }
        >
          {t("screens.empty")}
        </Empty>
      ) : null}
      {issues && screens.isSuccess && active.length > 0 && !visible.length && !powers.some((p) => p.isPending) ? <Empty icon="check">{t("screens.filter.none_to_handle")}</Empty> : null}
      {isAdmin && visible.length > 0 ? (
        <label className="select-all">
          <input type="checkbox" className="select-box" checked={allChosen} onChange={(e) => toggleAll(e.target.checked)} />
          <span>{t("screens.select.all", { count: visible.length })}</span>
          {visible.length > MAX_SELECTION ? <span className="meta">{t("actions.bar.cap", { max: MAX_SELECTION })}</span> : null}
        </label>
      ) : null}
      <ul className={`cards list-reset${chosen.length ? " has-selection" : ""}`}>
        {visible.map(({ s, power, state }) => (
          <ScreenCard
            key={s.id}
            screen={s}
            siteName={siteName(s.site_id)}
            groupName={groupName(s.group_id)}
            state={state}
            power={power}
            playback={playback.data?.find((p) => p.screen_id === s.id)}
            apk={apk?.screens.find((x) => x.screen_id === s.id)}
            availableApk={apk?.available ?? null}
            agent={agentOf(agents.data ?? [], s.site_id)}
            isAdmin={isAdmin}
            canForce={forcer}
            override={overrides.data?.find((o) => o.id === playback.data?.find((p) => p.screen_id === s.id)?.override_id)}
            allScreens={screens.data ?? []}
            onForce={() => setForce({ type: "screen", id: s.id })}
            provisionable={provisionable}
            selected={selection.has(s.id)}
            selectDisabled={selection.size >= MAX_SELECTION}
            onSelect={(on) => toggle(s.id, on)}
            onNotice={setNotice}
          />
        ))}
      </ul>
      {isAdmin && chosen.length ? <SelectionBar selected={chosen} agents={agents.data ?? []} groups={groups.data ?? []} sites={sites.data ?? []} apk={apk} onForce={() => setForce({ selection: chosen })} onClear={() => setSelection(new Set())} /> : null}
    </>
  );
}
