import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { authUiFor } from "../auth/providers";
import { useSession } from "../auth/session";
import { useApi } from "../lib/api";
import { useRuntimeConfig } from "../lib/runtime-config";
import { Icon } from "./Icon";
import { LanguageSwitch } from "./LanguageSwitch";

export function AppLayout() {
  const { t, i18n } = useTranslation();
  const config = useRuntimeConfig();
  const { auth, version } = config;
  const ui = authUiFor(auth.provider);
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const session = useSession();

  const logout = async () => {
    try {
      await ui?.logout(config, api);
    } catch {
      /* serveur injoignable : retour à la connexion quand même */
    } finally {
      qc.clear();
      navigate("/login", { replace: true });
    }
  };

  useEffect(() => {
    document.title = t("app.title");
  }, [t, i18n.language]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        {t("a11y.skip_to_content")}
      </a>
      <header className="app-header">
        <span className="vds-logo" role="img" aria-label={t("app.name")} />
        <nav className="app-nav" aria-label={t("nav.main")}>
          <NavLink to="/" end>
            {t("nav.home")}
          </NavLink>
          <NavLink to="/screens">
            <Icon name="screen" />
            {t("nav.screens")}
          </NavLink>
          <NavLink to="/sites">
            <Icon name="site" />
            {t("nav.sites")}
          </NavLink>
          <NavLink to="/media">
            <Icon name="media" />
            {t("nav.media")}
          </NavLink>
          <NavLink to="/playlists">
            <Icon name="playlist" />
            {t("nav.playlists")}
          </NavLink>
          <NavLink to="/schedules">
            <Icon name="schedule" />
            {t("nav.schedules")}
          </NavLink>
          <NavLink to="/power">
            <Icon name="power" />
            {t("nav.power")}
          </NavLink>
          <NavLink to="/supervision">
            <Icon name="incident" />
            {t("nav.supervision")}
          </NavLink>
          <NavLink to="/notifications">
            <Icon name="mail" />
            {t("nav.notifications")}
          </NavLink>
        </nav>
        <div className="app-tools">
          {auth.links.map((l) => (
            <a key={l.id} className="btn btn-text" href={l.href}>
              <Icon name="back" />
              {t(`nav.links.${l.id}`)}
            </a>
          ))}
          <LanguageSwitch />
          {session.data?.display_name ? (
            <span className="app-user">
              <Icon name="user" />
              <span>{session.data.display_name}</span>
            </span>
          ) : null}
          {ui ? (
            <button type="button" className="btn btn-text" onClick={() => void logout()}>
              <Icon name="logout" />
              {t("nav.logout")}
            </button>
          ) : auth.logoutUrl ? (
            <a className="btn btn-text" href={auth.logoutUrl}>
              <Icon name="logout" />
              {t("nav.logout")}
            </a>
          ) : null}
        </div>
      </header>
      <main id="main" className="app-main" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="app-footer">
        <span>{t("footer.version")}</span> <code>{version}</code>
      </footer>
    </div>
  );
}
