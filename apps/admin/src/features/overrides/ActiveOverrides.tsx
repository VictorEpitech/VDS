import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { Empty, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useAllGroups, useScreens, useSites } from "../../lib/queries";
import { OverrideControls, OverrideUntil } from "./OverrideControls";
import { OverrideForm } from "./OverrideForm";
import { canForce, screensOf, useOverrides } from "./overrides";

/** Bloc « Forçages en cours » de l'Accueil : fin visible, prolonger / arrêter, nouveau forçage. Administrateur et éditeur. */
export function ActiveOverrides() {
  const { t } = useTranslation();
  const session = useSession();
  const allowed = canForce(session.data?.role);
  const overrides = useOverrides(allowed);
  const screens = useScreens();
  const sites = useSites();
  const groups = useAllGroups();
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  if (!allowed) return null;

  const list = overrides.data ?? [];
  return (
    <section className="card override-block" aria-labelledby="home-overrides">
      <div className="override-block-head">
        <h2 id="home-overrides">
          <Icon name="overridden" />
          {t("overrides.block.title")}
        </h2>
        <button type="button" className="btn btn-text" aria-expanded={open} onClick={() => setOpen(true)}>
          <Icon name="add" />
          {t("overrides.force")}
        </button>
      </div>
      <QueryStatus query={overrides} />
      {notice ? <SuccessAlert>{notice}</SuccessAlert> : null}
      {open ? (
        <OverrideForm
          screens={screens.data ?? []}
          groups={groups.data ?? []}
          sites={sites.data ?? []}
          initial={null}
          isAdmin={session.data?.role === "admin"}
          onClose={() => setOpen(false)}
          onDone={(m) => {
            setOpen(false);
            setNotice(m);
          }}
        />
      ) : null}
      {overrides.isSuccess && list.length === 0 ? <Empty icon="check">{t("overrides.block.empty")}</Empty> : null}
      {list.length ? (
        <ul className="list-reset override-list">
          {list.map((o) => (
            <li key={o.id} className="card-inner">
              <p>
                <strong>{o.name || o.target_name}</strong>
                <span className="meta"> · {t(`overrides.target.${o.target_type}`, { name: o.target_name })}</span>
              </p>
              <p>
                <StatusBadge state="overridden" label={o.status === "scheduled" ? t("overrides.status.scheduled") : t("overrides.status.active")} />{" "}
                <OverrideUntil override={o} />
              </p>
              <p className="meta">{t("overrides.block.screens", { count: screensOf(o, screens.data ?? []).length })}</p>
              <OverrideControls override={o} screens={screens.data ?? []} label={o.target_name} isAdmin={session.data?.role === "admin"} onDone={setNotice} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
