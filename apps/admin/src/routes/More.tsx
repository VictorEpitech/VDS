import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { useSession } from "../auth/session";
import { useScope } from "../features/scope/scope";
import { ReopenOnboarding } from "../features/onboarding/OnboardingCard";
import { Icon, type IconName } from "../components/Icon";

interface SettingsEntry {
  to: string;
  icon: IconName;
  key: string;
  adminOnly?: boolean;
}

const ACCESS_ENTRIES: SettingsEntry[] = [
  { to: "/users", icon: "user", key: "users", adminOnly: true },
  { to: "/sites", icon: "site", key: "sites" },
];

const INFRA_ENTRIES: SettingsEntry[] = [
  { to: "/power", icon: "power", key: "power" },
  { to: "/notifications", icon: "mail", key: "notifications" },
];

/** Entrée « Plus » : hub de réglages (sites, utilisateurs, alimentation, notifications, assistance). */
export function More() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();
  const scope = useScope();

  // Éditeur restreint : interface réduite, seuls les réglages de son périmètre restent accessibles.
  const accessEntries = ACCESS_ENTRIES.filter((e) => (e.adminOnly ? scope.isAdmin : !scope.restricted));
  const infraEntries = INFRA_ENTRIES.filter((e) => (e.adminOnly ? scope.isAdmin : !scope.restricted));

  return (
    <div className="settings-page">
      <header className="page-header">
        <h1>{t("more.title")}</h1>
        <p className="lead">{t("more.intro")}</p>
      </header>

      <div className="settings-hub">
        {accessEntries.length > 0 && (
          <section className="settings-section" aria-labelledby="settings-heading-access">
            <h2 id="settings-heading-access" className="settings-section-title">
              <Icon name="site" />
              {t("more.section_access")}
            </h2>
            <ul className="settings-grid list-reset">
              {accessEntries.map((e) => (
                <li key={e.key} className="card settings-card">
                  <div className="settings-card-header">
                    <span className="settings-card-icon" aria-hidden="true">
                      <Icon name={e.icon} />
                    </span>
                    <div className="settings-card-body">
                      <h3>{t(`nav.${e.key}`)}</h3>
                      <p className="meta">{t(`more.${e.key}`)}</p>
                    </div>
                  </div>
                  <div className="settings-card-actions">
                    <Link className="btn btn-outline" to={e.to}>
                      {t("more.open")}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {infraEntries.length > 0 && (
          <section className="settings-section" aria-labelledby="settings-heading-infra">
            <h2 id="settings-heading-infra" className="settings-section-title">
              <Icon name="power" />
              {t("more.section_infra")}
            </h2>
            <ul className="settings-grid list-reset">
              {infraEntries.map((e) => (
                <li key={e.key} className="card settings-card">
                  <div className="settings-card-header">
                    <span className="settings-card-icon" aria-hidden="true">
                      <Icon name={e.icon} />
                    </span>
                    <div className="settings-card-body">
                      <h3>{t(`nav.${e.key}`)}</h3>
                      <p className="meta">{t(`more.${e.key}`)}</p>
                    </div>
                  </div>
                  <div className="settings-card-actions">
                    <Link className="btn btn-outline" to={e.to}>
                      {t("more.open")}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="card section settings-help-card" aria-labelledby="more-help">
          <h2 id="more-help">
            <Icon name="help" />
            {t("more.help.title")}
          </h2>
          <p className="meta">{t("more.help.intro")}</p>
          <div className="actions">
            <Link className="btn btn-filled" to="/help">
              {t("more.help.open")}
            </Link>
          </div>
          {session.data?.role === "admin" ? (
            <div className="settings-onboarding-box">
              <p className="meta">{t("more.help.onboarding")}</p>
              <ReopenOnboarding onDone={() => navigate("/", { state: { onboardingReopened: true } })} />
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
