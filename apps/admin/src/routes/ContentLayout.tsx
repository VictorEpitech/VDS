import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";
import { Icon } from "../components/Icon";

/** Entrée « Contenus » : médiathèque et playlists sous une même barre d'onglets. */
export function ContentLayout() {
  const { t } = useTranslation();
  return (
    <>
      <nav className="subnav" aria-label={t("nav.content")}>
        <NavLink to="/content/media">
          <Icon name="media" />
          {t("nav.media")}
        </NavLink>
        <NavLink to="/content/playlists">
          <Icon name="playlist" />
          {t("nav.playlists")}
        </NavLink>
        <NavLink to="/content/layouts">
          <Icon name="layout" />
          {t("nav.layouts")}
        </NavLink>
      </nav>
      <Outlet />
    </>
  );
}
