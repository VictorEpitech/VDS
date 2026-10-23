import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";
import { useRuntimeConfig } from "../lib/runtime-config";
import { Icon } from "./Icon";
import { LanguageSwitch } from "./LanguageSwitch";

export function AppLayout() {
  const { t, i18n } = useTranslation();
  const { auth, version } = useRuntimeConfig();

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
        </nav>
        <div className="app-tools">
          {auth.links.map((l) => (
            <a key={l.id} className="btn btn-text" href={l.href}>
              <Icon name="back" />
              {t(`nav.links.${l.id}`)}
            </a>
          ))}
          <LanguageSwitch />
          {auth.logoutUrl ? (
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
