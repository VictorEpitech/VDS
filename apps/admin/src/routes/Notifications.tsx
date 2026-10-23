import { useTranslation } from "react-i18next";
import { QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { ModuleSettingsForm } from "../features/settings/ModuleSettingsForm";
import { useOrganization } from "../lib/queries";

/** Notifications par e-mail (module notify.mail) : réglages organisation, formulaire généré. */
export function Notifications() {
  const { t } = useTranslation();
  const org = useOrganization();
  return (
    <>
      <h1>
        <Icon name="mail" />
        {t("notifications.title")}
      </h1>
      <p className="lead">{t("notifications.intro")}</p>
      <QueryStatus query={org} />
      {org.data ? <ModuleSettingsForm moduleId="notify.mail" level="organization" targetId={org.data.id} /> : null}
    </>
  );
}
