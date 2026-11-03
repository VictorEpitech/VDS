import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { authUiFor } from "../auth/providers";
import { useSession } from "../auth/session";
import { useApi } from "../lib/api";
import { useRuntimeConfig } from "../lib/runtime-config";
import { useScope } from "../features/scope/scope";
import { Empty } from "./feedback";
import { ActionQueueProvider } from "../features/actions/ActionQueueProvider";
import { Breadcrumbs } from "./Breadcrumbs";
import { Icon, type IconName } from "./Icon";
import { LanguageSwitch } from "./LanguageSwitch";

/** 5 entrées orientées intention ; « Plus » regroupe aussi les réglages (sites, alimentation, notifications). */
const NAV: { to: string; key: string; icon: IconName; also?: string[] }[] = [
  { to: "/", key: "home", icon: "home" },
  { to: "/screens", key: "screens", icon: "screen" },
  { to: "/content", key: "content", icon: "media" },
  { to: "/playback", key: "playback", icon: "schedule" },
  { to: "/more", key: "more", icon: "more", also: ["/sites", "/power", "/notifications", "/users"] },
];

export function AppLayout() {
  const { pathname } = useLocation();
  const { t, i18n } = useTranslation();
  const config = useRuntimeConfig();
  const { auth, version } = config;
  const ui = authUiFor(auth.provider);
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const session = useSession();
  const scope = useScope();
  // Éditeur restreint : « Plus » ne contient que l'aide ; l'entrée disparaît du menu.
  const nav = scope.restricted ? NAV.filter((n) => n.to !== "/more") : NAV;

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
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={n.also ? ({ isActive }) => (isActive || n.also?.some((p) => pathname.startsWith(p)) ? "active" : "") : undefined}>
              <Icon name={n.icon} />
              <span>{t(`nav.${n.key}`)}</span>
            </NavLink>
          ))}
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
        <Breadcrumbs />
        <ActionQueueProvider>
          {scope.empty ? (
            <Empty icon="lock">
              <span role="status">{t("scope.none")}</span>
            </Empty>
          ) : (
            <Outlet />
          )}
        </ActionQueueProvider>
      </main>
      <footer className="app-footer">
        <span>{t("footer.version")}</span> <code>{version}</code>
      </footer>
    </div>
  );
}
