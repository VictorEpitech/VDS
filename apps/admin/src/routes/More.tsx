import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { useSession } from "../auth/session";
import { useScope } from "../features/scope/scope";
import { ReopenOnboarding } from "../features/onboarding/OnboardingCard";
import { Icon, type IconName } from "../components/Icon";

const ENTRIES: { to: string; icon: IconName; key: string; adminOnly?: boolean }[] = [
  { to: "/users", icon: "user", key: "users", adminOnly: true },
  { to: "/sites", icon: "site", key: "sites" },
  { to: "/power", icon: "power", key: "power" },
  { to: "/notifications", icon: "mail", key: "notifications" },
];


/** Entrée « Plus » : réglages (sites et agents, alimentation, notifications). */
export function More() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();
  const scope = useScope();
  // Éditeur restreint : interface réduite, seuls les réglages de son périmètre restent accessibles (les autres sont refusés par le serveur).
  const entries = ENTRIES.filter((e) => (e.adminOnly ? scope.isAdmin : !scope.restricted));
  return (
    <>
      <h1>{t("more.title")}</h1>
      <p className="lead">{t("more.intro")}</p>
      <ul className="cards list-reset">
        {entries.map((e) => (
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
      <section className="card section" aria-labelledby="more-help">
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
          <>
            <p className="meta">{t("more.help.onboarding")}</p>
            <ReopenOnboarding onDone={() => navigate("/", { state: { onboardingReopened: true } })} />
          </>
        ) : null}
      </section>
    </>
  );
}
