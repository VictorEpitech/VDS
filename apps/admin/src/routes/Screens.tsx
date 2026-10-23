import { useTranslation } from "react-i18next";

export function Screens() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("screens.title")}</h1>
      <p className="lead">{t("screens.empty")}</p>
    </>
  );
}
