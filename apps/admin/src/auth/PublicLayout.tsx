import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router-dom";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { useRuntimeConfig } from "../lib/runtime-config";

/** Cadre des écrans d'accès (connexion, amorçage, changement de mot de passe) : logo et langue, sans navigation. */
export function PublicFrame({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { version } = useRuntimeConfig();

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
        <div className="app-nav" />
        <div className="app-tools">
          <LanguageSwitch />
        </div>
      </header>
      <main id="main" className="app-main auth-main" tabIndex={-1}>
        {children}
      </main>
      <footer className="app-footer">
        <span>{t("footer.version")}</span> <code>{version}</code>
      </footer>
    </div>
  );
}

export function PublicLayout() {
  return (
    <PublicFrame>
      <Outlet />
    </PublicFrame>
  );
}

/** Carte d'un écran d'accès : titre, introduction, contenu. */
export function AuthCard({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="card auth-card" aria-labelledby="auth-title">
      <h1 id="auth-title">{title}</h1>
      {intro ? <p className="lead">{intro}</p> : null}
      {children}
    </section>
  );
}
