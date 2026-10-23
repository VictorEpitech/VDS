import { useTranslation } from "react-i18next";
import { IncidentsList } from "../features/supervision/IncidentsList";
import { ScreensStatus } from "../features/supervision/ScreensStatus";

export function Supervision() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("supervision.title")}</h1>
      <p className="lead">{t("supervision.intro")}</p>
      <IncidentsList />
      <ScreensStatus />
    </>
  );
}
