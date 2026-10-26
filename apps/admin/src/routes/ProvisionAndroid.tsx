import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useSession } from "../auth/session";
import { Empty, QueryStatus } from "../components/feedback";
import { ProvisionWizard } from "../features/provisioning/Wizard";

/** Assistant « Ajouter une TV Android » : réservé au rôle admin (le serveur le refuse de toute façon). */
export function ProvisionAndroid() {
  const { t } = useTranslation();
  const session = useSession();
  if (!session.isSuccess) return <QueryStatus query={session} />;
  if (session.data.role !== "admin") {
    return (
      <>
        <h1>{t("provision.title")}</h1>
        <Empty icon="lock">{t("provision.admin_only")}</Empty>
        <Link className="btn btn-text" to="/screens">
          {t("pairing.back")}
        </Link>
      </>
    );
  }
  return <ProvisionWizard />;
}
