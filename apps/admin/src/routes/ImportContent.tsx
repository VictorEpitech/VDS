import { useTranslation } from "react-i18next";
import { ImportWizard } from "../features/media/import/ImportWizard";

/** Point d'entrée « Ajouter un contenu » : assistant d'import multi-fichiers. */
export function ImportContent() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("import.title")}</h1>
      <p className="lead">{t("import.intro")}</p>
      <ImportWizard />
    </>
  );
}
