import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "../components/Icon";

const ENTRIES: { to: string; icon: IconName; key: string }[] = [
  { to: "/sites", icon: "site", key: "sites" },
  { to: "/power", icon: "power", key: "power" },
  { to: "/notifications", icon: "mail", key: "notifications" },
];

/** Entrée « Plus » : réglages (sites et agents, alimentation, notifications). */
export function More() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("more.title")}</h1>
      <p className="lead">{t("more.intro")}</p>
      <ul className="cards list-reset">
        {ENTRIES.map((e) => (
          <li key={e.key} className="card">
            <h2>
              <Icon name={e.icon} />
              {t(`nav.${e.key}`)}
            </h2>
            <p className="meta">{t(`more.${e.key}`)}</p>
            <div className="actions">
              <Link className="btn btn-filled" to={e.to}>
                {t("more.open")}
              </Link>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
