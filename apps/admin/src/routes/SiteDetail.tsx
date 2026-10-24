import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ConfirmAction, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { GroupsPanel } from "../features/sites/GroupsPanel";
import { AgentsPanel } from "../features/sites/AgentsPanel";
import { AttachmentPanel } from "../features/power/AttachmentSection";
import { useApi } from "../lib/api";
import { keys, useSites } from "../lib/queries";
import { NotFound } from "./NotFound";

export function SiteDetail() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const sites = useSites();
  const site = sites.data?.find((s) => s.id === id);
  const archive = useMutation({
    mutationFn: () => api.patch(`/sites/${id}`, { archived: true }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.sites });
      navigate("/sites");
    },
  });
  if (sites.isSuccess && !site) return <NotFound />;
  return (
    <>
      <p>
        <Link to="/sites" className="btn btn-text">
          <Icon name="back" />
          {t("sites.back")}
        </Link>
      </p>
      <QueryStatus query={sites} />
      {site ? (
        <>
          <div className="page-head">
            <h1>
              <Icon name="site" />
              {site.name}
            </h1>
            <ConfirmAction label={t("sites.archive.action")} confirmLabel={t("sites.archive.confirm", { name: site.name })} pending={archive.isPending} onConfirm={() => archive.mutate()} />
          </div>
          {archive.isError ? <ErrorAlert error={archive.error} /> : null}
          <p className="meta">
            <code>{site.slug}</code>
          </p>
          <GroupsPanel siteId={site.id} />
          <AgentsPanel siteId={site.id} />
          <section className="section" aria-labelledby="site-power-h">
            <h2 id="site-power-h">
              <Icon name="power" />
              {t("power.attachment.title")}
            </h2>
            <AttachmentPanel level="site" id={site.id} />
          </section>
        </>
      ) : null}
    </>
  );
}
