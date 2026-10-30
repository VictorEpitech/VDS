import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ConfirmAction, ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { agentKeys } from "../../lib/queries";

type S = components["schemas"];
type Enrollment = S["EnrollmentOut"];
type Created = S["EnrollmentCreated"];
type LocalAgent = S["LocalAgentOut"];

const POLL_MS = 3000;
const enrollKeys = {
  list: (siteId: string) => ["enrollments", siteId] as const,
  local: ["agent", "local"] as const,
};

/** Canal local : un échec (hébergé, droits) équivaut à « indisponible » ; le bouton reste simplement masqué. */
function useLocalAgent() {
  const api = useApi();
  return useQuery({
    queryKey: enrollKeys.local,
    queryFn: () => api.get<LocalAgent>("/agent/local"),
    retry: false,
    refetchInterval: (q) => (q.state.data?.available && !q.state.data.detected ? POLL_MS : false),
  });
}

function useEnrollments(siteId: string, live: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: enrollKeys.list(siteId),
    queryFn: () => api.get<Enrollment[]>(`/sites/${siteId}/agent/enrollments`),
    refetchInterval: (q) => (live || q.state.data?.some((e) => e.state === "pending") ? POLL_MS : false),
  });
}

/** Téléchargement de agent.env : URL de données locale, jamais envoyée ailleurs. */
function EnvDownload({ content }: { content: string }) {
  const { t } = useTranslation();
  const url = `data:text/plain;charset=utf-8,${encodeURIComponent(content)}`;
  return (
    <a className="btn btn-text" href={url} download="agent.env">
      <Icon name="upload" />
      {t("enroll.remote.download_env")}
    </a>
  );
}

function CopyBlock({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="field">
      <p className="meta">{label}</p>
      <code className="token mono" data-testid="enroll-command">
        {value}
      </code>
      <div className="actions">
        <button type="button" className="btn btn-text" onClick={() => void copy()}>
          <Icon name="copy" />
          {t("enroll.copy")}
        </button>
      </div>
      {copied ? (
        <p className="meta" role="status">
          {t("enroll.copied")}
        </p>
      ) : null}
    </div>
  );
}

/** Code d'enrôlement : affiché une seule fois, gardé uniquement dans l'état du composant. */
function CodeReveal({ created, when, onDismiss }: { created: Created; when: (iso: string) => string; onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <section className="token-box" aria-labelledby="enroll-code-title">
      <h4 id="enroll-code-title">
        <Icon name="warning" />
        {t("enroll.remote.title")}
      </h4>
      <p>{t("enroll.remote.once")}</p>
      <p className="meta">
        <Icon name="schedule" /> {t("enroll.remote.expires", { when: when(created.expires_at) })}
      </p>
      {created.commands.docker_login ? (
        <CopyBlock label={t("enroll.remote.login")} value={created.commands.docker_login} />
      ) : (
        <p className="meta">{t("enroll.remote.no_login")}</p>
      )}
      <CopyBlock label={t("enroll.remote.run")} value={created.commands.docker_run} />
      <p className="meta">{t("enroll.remote.env_help")}</p>
      <div className="actions">
        <EnvDownload content={created.files.agent_env} />
        <button type="button" className="btn btn-text" onClick={onDismiss}>
          <Icon name="check" />
          {t("enroll.remote.done")}
        </button>
      </div>
    </section>
  );
}

export function EnrollPanel({ siteId }: { siteId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const local = useLocalAgent();
  const [tracked, setTracked] = useState<string | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const enrollments = useEnrollments(siteId, tracked !== null);
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: enrollKeys.list(siteId) });
    await qc.invalidateQueries({ queryKey: agentKeys.site(siteId) });
  };
  const activate = useMutation({
    mutationFn: () => api.post<S["LocalActivated"]>(`/sites/${siteId}/agent/local`),
    onSuccess: async (r) => {
      setTracked(r.enrollment_id);
      await refresh();
    },
  });
  const generate = useMutation({
    mutationFn: () => api.post<Created>(`/sites/${siteId}/agent/enrollments`, { ttl_min: 60 }),
    onSuccess: async (r) => {
      setCreated(r);
      setTracked(r.id);
      await refresh();
    },
  });
  const cancel = useMutation({
    mutationFn: (e: Enrollment) => api.delete(`/sites/${siteId}/agent/enrollments/${e.id}`),
    onSuccess: refresh,
  });
  // « Ce n'est pas mon agent » : l'inscription est consommée, on révoque l'agent arrivé puis on repart de zéro.
  const reject = useMutation({
    mutationFn: (e: Enrollment) => api.post(`/sites/${siteId}/agents/${e.agent_id}/revoke`),
    onSuccess: async () => {
      setTracked(null);
      setCreated(null);
      await refresh();
    },
  });

  const when = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  const list = enrollments.data ?? [];
  const pending = list.filter((e) => e.state === "pending");
  const arrived = tracked ? list.find((e) => e.id === tracked && e.state === "consumed") : undefined;
  const waiting = tracked ? list.find((e) => e.id === tracked && e.state === "pending") : undefined;
  const lapsed = tracked ? list.find((e) => e.id === tracked && (e.state === "expired" || e.state === "revoked")) : undefined;
  const available = local.data?.available === true;
  const detected = local.data?.detected ?? null;

  return (
    <div className="card" role="group" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>
        <Icon name="add" />
        {t("enroll.title")}
      </h3>
      <p>{t("enroll.intro")}</p>

      {activate.isError ? <ErrorAlert error={activate.error} /> : null}
      {generate.isError ? <ErrorAlert error={generate.error} /> : null}
      {cancel.isError ? <ErrorAlert error={cancel.error} /> : null}
      {reject.isError ? <ErrorAlert error={reject.error} /> : null}

      {arrived ? (
        <section className="alert alert-success" role="status" aria-labelledby={`${uid}-arr`}>
          <Icon name="online" />
          <div>
            <p className="alert-text" id={`${uid}-arr`}>
              {t("enroll.arrived.title")}
            </p>
            <dl className="device-facts">
              <div>
                <dt>{t("enroll.arrived.name")}</dt> <dd>{arrived.consumed_hostname ?? t("enroll.arrived.unknown")}</dd>
              </div>
              <div>
                <dt>{t("enroll.arrived.ip")}</dt> <dd className="mono">{arrived.consumed_ip ?? t("enroll.arrived.unknown")}</dd>
              </div>
            </dl>
            <div className="actions">
              <button type="button" className="btn btn-text" onClick={() => setTracked(null)}>
                <Icon name="check" />
                {t("enroll.arrived.ok")}
              </button>
              {arrived.agent_id ? (
                <ConfirmAction icon="revoke" label={t("enroll.arrived.not_mine")} confirmLabel={t("enroll.arrived.not_mine_confirm")} pending={reject.isPending} onConfirm={() => reject.mutate(arrived)} />
              ) : null}
            </div>
          </div>
        </section>
      ) : null}

      {waiting && !arrived ? (
        <p className="alert alert-info" role="status">
          <Icon name="processing" />
          <span>{t("enroll.waiting", { when: when(waiting.expires_at) })}</span>
        </p>
      ) : null}
      {lapsed ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t(`enroll.lapsed.${lapsed.state}`)}</span>
        </p>
      ) : null}

      {created ? <CodeReveal created={created} when={when} onDismiss={() => setCreated(null)} /> : null}

      <div className="actions">
        {available ? (
          <button type="button" className="btn btn-filled" disabled={activate.isPending || !detected} aria-describedby={`${uid}-local`} aria-busy={activate.isPending} onClick={() => activate.mutate()}>
            <Icon name="agent" />
            {t("enroll.local.action")}
          </button>
        ) : null}
        <button type="button" className={available ? "btn btn-text" : "btn btn-filled"} disabled={generate.isPending} aria-busy={generate.isPending} onClick={() => generate.mutate()}>
          <Icon name="add" />
          {t("enroll.remote.action")}
        </button>
      </div>
      {available ? (
        <p className="field-help" id={`${uid}-local`}>
          {detected ? t("enroll.local.detected", { host: detected.hostname }) : t("enroll.local.not_detected")}
        </p>
      ) : null}

      {pending.length > 0 ? (
        <>
          <h4>{t("enroll.pending.title")}</h4>
          <ul className="list-reset">
            {pending.map((e) => (
              <li key={e.id} className="actions">
                <span>
                  <Icon name="pairing" /> {t(`enroll.channel.${e.channel}`)} - {t("enroll.pending.expires", { when: when(e.expires_at) })}
                </span>
                <ConfirmAction icon="revoke" label={t("enroll.pending.revoke")} confirmLabel={t("enroll.pending.revoke_confirm")} pending={cancel.isPending} onConfirm={() => cancel.mutate(e)} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

/** Aide « aucun agent » : renvoie vers le panneau Site › Agent. */
export function AgentSetupLink({ siteId }: { siteId?: string }) {
  const { t } = useTranslation();
  return (
    <Link className="btn btn-text" to={siteId ? `/sites/${siteId}` : "/sites"}>
      <Icon name="agent" />
      {t("enroll.setup_link")}
    </Link>
  );
}
