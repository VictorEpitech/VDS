import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useSession } from "../auth/session";
import { KioskControls } from "../features/kiosk/KioskControls";
import { KioskSetting } from "../features/kiosk/KioskSetting";
import { ScreenCreateForm } from "../features/screens/ScreenCreateForm";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus, SuccessAlert } from "../components/feedback";
import { Icon } from "../components/Icon";
import { PresenceBadge } from "../components/StatusBadge";
import { useApi } from "../lib/api";
import { keys, useAllGroups, useScreens, useSites, type Screen } from "../lib/queries";

export function Screens() {
  const { t, i18n } = useTranslation();
  const screens = useScreens();
  const sites = useSites();
  const groups = useAllGroups();
  const api = useApi();
  const qc = useQueryClient();
  const session = useSession();
  const isAdmin = session.data?.role === "admin";
  const [creating, setCreating] = useState(false);
  const [revoked, setRevoked] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: (s: Screen) => api.delete(`/screens/${s.id}/device`),
    onMutate: () => setRevoked(null),
    onSuccess: (_d, s) => {
      setRevoked(s.name);
      void qc.invalidateQueries({ queryKey: keys.screens });
    },
  });

  const siteName = (id: string) => sites.data?.find((s) => s.id === id)?.name ?? "";
  const groupName = (id: string | null) => (id ? (groups.data?.find((g) => g.id === id)?.name ?? "") : "");
  const when = (iso: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  const active = (screens.data ?? []).filter((s) => !s.archived_at);

  return (
    <>
      <div className="page-head">
        <h1>{t("screens.title")}</h1>
        <div className="actions">
          {isAdmin ? (
            <button type="button" className="btn btn-filled" onClick={() => setCreating(true)} disabled={creating}>
              <Icon name="add" />
              {t("screens.create.action")}
            </button>
          ) : null}
          {isAdmin ? (
            <Link className="btn btn-text" to="/screens/android">
              <Icon name="detect" />
              {t("provision.action")}
            </Link>
          ) : null}
          <Link className="btn btn-text" to="/pairing">
            <Icon name="pairing" />
            {t("screens.pair")}
          </Link>
        </div>
      </div>
      <p className="lead">{t("screens.intro")}</p>
      {creating ? <ScreenCreateForm onClose={() => setCreating(false)} /> : null}
      <QueryStatus query={screens} />
      {revoked ? <SuccessAlert>{t("screens.revoked", { name: revoked })}</SuccessAlert> : null}
      {revoke.isError ? (
        <ErrorAlert error={revoke.error} />
      ) : null}
      {screens.isSuccess && active.length === 0 ? (
        <Empty icon="screen">
          {t("screens.empty")}
          {isAdmin && !creating ? (
            <>
              {" "}
              <button type="button" className="btn btn-filled" onClick={() => setCreating(true)}>
                <Icon name="add" />
                {t("screens.create.action")}
              </button>
            </>
          ) : null}
        </Empty>
      ) : null}
      <ul className="cards list-reset">
        {active.map((s) => (
          <li key={s.id} className="card" aria-labelledby={`screen-${s.id}`}>
            <h2 id={`screen-${s.id}`}>
              <Icon name="screen" />
              {s.name}
            </h2>
            <p className="meta">
              {[siteName(s.site_id), groupName(s.group_id), t(`screens.orientation.${s.orientation}`)].filter(Boolean).join(" · ")}
            </p>
            {s.device ? (
              <>
                <p>
                  <PresenceBadge presence={s.device.presence} />
                </p>
                <dl className="device-facts">
                  <div>
                    <dt>{t("screens.device.platform")}</dt> <dd className="mono">{[s.device.platform, s.device.model].filter(Boolean).join(" ")}</dd>
                  </div>
                  {s.device.app_version ? (
                    <div>
                      <dt>{t("screens.device.version")}</dt> <dd className="mono">{s.device.app_version}</dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>{t("screens.device.last_seen")}</dt>{" "}
                    <dd>{s.device.last_seen_at ? when(s.device.last_seen_at) : t("screens.device.never_seen")}</dd>
                  </div>
                  {s.device.revoked_at ? (
                    <div>
                      <dt>{t("screens.device.revoked_at")}</dt> <dd>{when(s.device.revoked_at)}</dd>
                    </div>
                  ) : null}
                </dl>
              </>
            ) : (
              <p className="meta">{t("screens.device.none")}</p>
            )}
            <KioskControls screen={s} />
            {isAdmin ? <KioskSetting level="screen" id={s.id} /> : null}
            <div className="actions">
              <Link className="btn btn-text" to={`/pairing?screen=${s.id}`}>
                <Icon name="pairing" />
                {t("screens.pair_this")}
              </Link>
              {s.device && !s.device.revoked_at && s.device.presence !== "revoked" ? (
                <ConfirmAction
                  icon="revoke"
                  label={t("screens.revoke.action")}
                  confirmLabel={t("screens.revoke.confirm", { name: s.name })}
                  pending={revoke.isPending}
                  onConfirm={() => revoke.mutate(s)}
                />
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
