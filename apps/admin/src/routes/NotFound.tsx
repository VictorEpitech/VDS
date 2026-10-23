import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";

export function NotFound() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("errors.not_found.title")}</h1>
      <p className="lead">{t("errors.not_found.body")}</p>
      <Link className="btn btn-filled" to="/">
        <Icon name="back" />
        {t("errors.not_found.back")}
      </Link>
    </>
  );
}
