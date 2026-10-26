import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";

const COUNT = 5;
/** Index du cas « compte Google » dans le tutoriel du module (mis en évidence). */
const GOOGLE_ACCOUNT = 3;

/** Tutoriel pas à pas : développeur, débogage réseau, cas compte Google, adresse IP, invite RSA. */
export function Tutorial() {
  const { t } = useTranslation();
  return (
    <ol className="tutorial">
      {Array.from({ length: COUNT }, (_, i) => i + 1).map((n) => (
        <li key={n} className={n === GOOGLE_ACCOUNT ? "tutorial-note" : undefined}>
          <h3>
            {n === GOOGLE_ACCOUNT ? <Icon name="warning" /> : null}
            {t(`provision.tutorial.${n}.title`)}
          </h3>
          <p>{t(`provision.tutorial.${n}.body`)}</p>
        </li>
      ))}
    </ol>
  );
}
