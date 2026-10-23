import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { useIncidents, type Incident } from "../../lib/queries";

type Filter = "open" | "resolved" | "all";

function IncidentState({ i }: { i: Incident }) {
  const { t } = useTranslation();
  if (i.resolved_at) return <StatusBadge state="online" icon="check" label={t("supervision.incidents.state.resolved")} />;
  if (i.acknowledged_at) return <StatusBadge state="pairing" icon="check" label={t("supervision.incidents.state.acknowledged")} />;
  return i.severity === "critical" ? (
    <StatusBadge state="offline" icon="offline" label={t("supervision.incidents.state.open")} />
  ) : (
    <StatusBadge state="power_failed" icon="warning" label={t("supervision.incidents.state.open")} />
  );
}

export function IncidentsList() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const session = useSession();
  const [filter, setFilter] = useState<Filter>("open");
  const incidents = useIncidents(filter);
  const ack = useMutation({
    mutationFn: (i: Incident) => api.post<Incident>(`/incidents/${i.id}/acknowledge`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["incidents"] }),
  });
  const isAdmin = session.data?.role === "admin";
  const when = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  return (
    <section className="section" aria-labelledby={`${uid}-h`}>
      <div className="page-head">
        <h2 id={`${uid}-h`}>
          <Icon name="incident" />
          {t("supervision.incidents.title")}
        </h2>
        <div className="field">
          <label htmlFor={`${uid}-f`}>{t("supervision.incidents.filter")}</label>
          <select id={`${uid}-f`} value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            {(["open", "resolved", "all"] as const).map((f) => (
              <option key={f} value={f}>
                {t(`supervision.incidents.filters.${f}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <QueryStatus query={incidents} />
      {ack.isError ? <ErrorAlert error={ack.error} /> : null}
      {incidents.isSuccess && incidents.data.length === 0 ? <Empty icon="check">{t("supervision.incidents.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {(incidents.data ?? []).map((i) => {
          const label = t(`supervision.incidents.kinds.${i.kind}`, { defaultValue: i.kind });
          return (
            <li key={i.id} className="card" aria-labelledby={`inc-${i.id}`}>
              <h3 id={`inc-${i.id}`}>
                <Icon name="incident" />
                {label}
              </h3>
              <p>
                <IncidentState i={i} />
              </p>
              <p className="meta">{[i.subject_name, i.site_name].filter(Boolean).join(" · ")}</p>
              <p className="meta">{t("supervision.incidents.opened", { when: when(i.opened_at) })}</p>
              {i.resolved_at ? <p className="meta">{t("supervision.incidents.resolved", { when: when(i.resolved_at) })}</p> : null}
              {isAdmin && !i.resolved_at && !i.acknowledged_at ? (
                <div className="actions">
                  <button type="button" className="btn btn-text" disabled={ack.isPending} onClick={() => ack.mutate(i)} aria-label={t("supervision.incidents.ack_for", { name: label })}>
                    <Icon name="check" />
                    {t("supervision.incidents.ack")}
                  </button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
