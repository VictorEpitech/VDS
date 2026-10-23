import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { agentKeys, useAgents, type Agent, type AgentWithToken } from "../../lib/queries";

const STATE: Record<Agent["status"], { state: "online" | "offline" | "pairing" | "revoked"; icon: "online" | "offline" | "pairing" | "revoke" }> = {
  online: { state: "online", icon: "online" },
  offline: { state: "offline", icon: "offline" },
  unseen: { state: "pairing", icon: "pairing" },
  revoked: { state: "revoked", icon: "revoke" },
};

export function AgentStatusBadge({ status }: { status: Agent["status"] }) {
  const { t } = useTranslation();
  const { state, icon } = STATE[status];
  return <StatusBadge state={state} icon={icon} label={t(`agents.status.${status}`)} />;
}

/** Jeton d'agent : affiché une seule fois, gardé uniquement en mémoire du composant (jamais en cache de requête). */
function TokenReveal({ result, onDismiss }: { result: AgentWithToken; onDismiss: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result.token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <section className="token-box" role="alert" aria-labelledby="token-title">
      <h3 id="token-title">
        <Icon name="warning" />
        {t("agents.token.title", { name: result.agent.name })}
      </h3>
      <p>{t("agents.token.once")}</p>
      <p>
        <code className="token" data-testid="agent-token">
          {result.token}
        </code>
      </p>
      <div className="actions">
        <button type="button" className="btn btn-filled" onClick={() => void copy()}>
          <Icon name="copy" />
          {t("agents.token.copy")}
        </button>
        <button type="button" className="btn btn-text" onClick={onDismiss}>
          <Icon name="check" />
          {t("agents.token.done")}
        </button>
      </div>
      {copied ? (
        <p className="meta" role="status">
          {t("agents.token.copied")}
        </p>
      ) : null}
    </section>
  );
}

export function AgentsPanel({ siteId }: { siteId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const agents = useAgents(siteId);
  const [name, setName] = useState("");
  const [revealed, setRevealed] = useState<AgentWithToken | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: agentKeys.site(siteId) });
  const create = useMutation({
    mutationFn: (n: string) => api.post<AgentWithToken>(`/sites/${siteId}/agents`, { name: n }),
    onSuccess: async (r) => {
      setRevealed(r);
      setName("");
      await refresh();
    },
  });
  const rotate = useMutation({
    mutationFn: (a: Agent) => api.post<AgentWithToken>(`/sites/${siteId}/agents/${a.id}/rotate`),
    onSuccess: async (r) => {
      setRevealed(r);
      await refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: (a: Agent) => api.post<Agent>(`/sites/${siteId}/agents/${a.id}/revoke`),
    onSuccess: refresh,
  });
  const when = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : t("agents.never_seen");
  const list = agents.data ?? [];
  const hasActive = list.some((a) => a.status !== "revoked");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  return (
    <section className="section" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>
        <Icon name="agent" />
        {t("agents.title")}
      </h2>
      <p className="lead">{t("agents.intro")}</p>
      <QueryStatus query={agents} />
      {revealed ? <TokenReveal result={revealed} onDismiss={() => setRevealed(null)} /> : null}
      {create.isError ? <ErrorAlert error={create.error} /> : null}
      {rotate.isError ? <ErrorAlert error={rotate.error} /> : null}
      {revoke.isError ? <ErrorAlert error={revoke.error} /> : null}
      {agents.isSuccess && list.length === 0 ? <Empty icon="agent">{t("agents.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {list.map((a) => (
          <li key={a.id} className="card" aria-labelledby={`${uid}-${a.id}`}>
            <h3 id={`${uid}-${a.id}`}>
              <Icon name="agent" />
              {a.name}
            </h3>
            <p>
              <AgentStatusBadge status={a.status} />
            </p>
            <dl className="device-facts">
              <div>
                <dt>{t("agents.token_prefix")}</dt> <dd className="mono">{a.token_prefix}…</dd>
              </div>
              {a.version ? (
                <div>
                  <dt>{t("screens.device.version")}</dt> <dd className="mono">{a.version}</dd>
                </div>
              ) : null}
              <div>
                <dt>{t("screens.device.last_seen")}</dt> <dd>{when(a.last_seen_at)}</dd>
              </div>
              {a.rotated_at ? (
                <div>
                  <dt>{t("agents.rotated_at")}</dt> <dd>{when(a.rotated_at)}</dd>
                </div>
              ) : null}
              {a.revoked_at ? (
                <div>
                  <dt>{t("screens.device.revoked_at")}</dt> <dd>{when(a.revoked_at)}</dd>
                </div>
              ) : null}
            </dl>
            {a.status !== "revoked" ? (
              <div className="actions">
                <ConfirmAction icon="rotate" label={t("agents.rotate.action")} confirmLabel={t("agents.rotate.confirm", { name: a.name })} pending={rotate.isPending} onConfirm={() => rotate.mutate(a)} />
                <ConfirmAction icon="revoke" label={t("agents.revoke.action")} confirmLabel={t("agents.revoke.confirm", { name: a.name })} pending={revoke.isPending} onConfirm={() => revoke.mutate(a)} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {hasActive ? (
        <p className="meta">{t("agents.one_active")}</p>
      ) : (
        <form className="card inline-form" onSubmit={submit} aria-label={t("agents.create.title")}>
          <h3>{t("agents.create.title")}</h3>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("agents.create.name")}</label>
            <input id={`${uid}-name`} value={name} maxLength={120} required autoComplete="off" onChange={(e) => setName(e.target.value)} />
            <p className="field-help">{t("agents.create.help")}</p>
          </div>
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={create.isPending || !name.trim()} aria-busy={create.isPending}>
              <Icon name="add" />
              {t("agents.create.submit")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
