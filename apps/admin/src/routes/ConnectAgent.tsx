import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { useSession } from "../auth/session";
import { Empty, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { ConnectAgentWizard } from "../features/agents/ConnectAgentWizard";
import { useSites } from "../lib/queries";

/** Point d'entrée « Connecter un agent » : choix du site (préselectionné s'il est seul ou donné par `?site=`), puis assistant. */
export function ConnectAgent() {
  const { t } = useTranslation();
  const uid = useId();
  const session = useSession();
  const sites = useSites();
  const [params, setParams] = useSearchParams();
  if (!session.isSuccess) return <QueryStatus query={session} />;
  const active = (sites.data ?? []).filter((s) => !s.archived_at);
  const chosen = params.get("site") ?? (active.length === 1 ? active[0]!.id : "");
  return (
    <>
      <h1>{t("agents.connect.title")}</h1>
      <p className="lead">{t("agents.connect.intro")}</p>
      <QueryStatus query={sites} />
      {session.data.role !== "admin" ? (
        <Empty icon="lock">{t("agents.connect.admin_only")}</Empty>
      ) : sites.isSuccess && active.length === 0 ? (
        <Empty
          icon="site"
          action={
            <Link className="btn btn-filled" to="/sites">
              <Icon name="add" />
              {t("sites.empty_action")}
            </Link>
          }
        >
          {t("agents.connect.no_site")}
        </Empty>
      ) : sites.isSuccess && !chosen ? (
        <div className="card form-narrow field">
          <label htmlFor={`${uid}-site`}>{t("agents.connect.site")}</label>
          <select id={`${uid}-site`} value="" onChange={(e) => setParams({ site: e.target.value })}>
            <option value="">{t("form.choose")}</option>
            {active.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      ) : chosen ? (
        <ConnectAgentWizard key={chosen} siteId={chosen} />
      ) : null}
    </>
  );
}
