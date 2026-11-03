import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { ReviewQueue } from "../features/publication/ReviewQueue";
import { useIsAdmin } from "../features/publication/publication";

export function Review() {
  const { t } = useTranslation();
  const isAdmin = useIsAdmin();
  return (
    <>
      <p>
        <Link to="/playback">{t("publication.review.back")}</Link>
      </p>
      <h1>{t("publication.review.title")}</h1>
      <p className="lead">{t("publication.review.intro")}</p>
      {isAdmin ? (
        <ReviewQueue />
      ) : (
        <p className="alert alert-warning" role="status">
          <Icon name="lock" />
          <span>{t("publication.review.admin_only")}</span>
        </p>
      )}
    </>
  );
}
