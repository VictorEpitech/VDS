import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useSession } from "../auth/session";
import { ReopenOnboarding } from "../features/onboarding/OnboardingCard";
import { useHelpBody } from "../features/help/HelpPanel";
import { GLOSSARY_KEYS, HELP_TOPICS } from "../features/help/topics";
import { Icon } from "../components/Icon";

/** Page Aide (Plus) : Premiers pas, sujets d'aide et glossaire. Textes en clés i18n, aucun lien externe. */
export function Help() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();
  const body = useHelpBody();
  return (
    <>
      <h1>{t("help.page.title")}</h1>
      <p className="lead">{t("help.page.intro")}</p>

      {session.data?.role === "admin" ? (
        <section className="card section" aria-labelledby="help-onboarding">
          <h2 id="help-onboarding">
            <Icon name="home" />
            {t("more.help.title")}
          </h2>
          <p className="meta">{t("more.help.onboarding")}</p>
          <ReopenOnboarding onDone={() => navigate("/", { state: { onboardingReopened: true } })} />
        </section>
      ) : null}

      <section className="section" aria-labelledby="help-topics">
        <h2 id="help-topics">{t("help.page.topics")}</h2>
        <ul className="cards list-reset">
          {HELP_TOPICS.map((topic) => (
            <li key={topic} className="card">
              <h3>
                <Icon name="help" />
                {t(`help.${topic}.title`)}
              </h3>
              <p>{body(topic)}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="section" aria-labelledby="help-glossary">
        <h2 id="help-glossary">{t("help.page.glossary")}</h2>
        <dl className="glossary">
          {GLOSSARY_KEYS.map((k) => (
            <div key={k}>
              <dt>{t(`glossary.${k}.term`)}</dt>
              <dd>{t(`glossary.${k}.definition`)}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}
