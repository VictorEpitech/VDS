import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useSession } from "../auth/session";
import { Empty, QueryStatus } from "../components/feedback";
import { AddScreen as AddScreenWizard } from "../features/screens/add/AddScreen";
import { ProvisionWizard } from "../features/provisioning/Wizard";

/** Point d'entrée unique « Ajouter un écran » (admin : le serveur refuse les autres rôles de toute façon). */
export function AddScreen({ android = false }: { android?: boolean }) {
  const { t } = useTranslation();
  const session = useSession();
  if (!session.isSuccess) return <QueryStatus query={session} />;
  if (session.data.role !== "admin") {
    return (
      <>
        <h1>{t("add.title")}</h1>
        <Empty icon="lock">{t("provision.admin_only")}</Empty>
        <Link className="btn btn-text" to="/screens">
          {t("pairing.back")}
        </Link>
      </>
    );
  }
  return android ? <ProvisionWizard /> : <AddScreenWizard />;
}
