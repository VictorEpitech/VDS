import type { Locale } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { LOCALES, storeLocale } from "../lib/i18n";
import { Icon } from "./Icon";

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  const choose = (l: Locale) => {
    storeLocale(l);
    void i18n.changeLanguage(l);
  };
  return (
    <div className="lang-switch" role="group" aria-label={t("language.label")}>
      <Icon name="language" />
      {LOCALES.map((l) => (
        <button
          key={l}
          type="button"
          className="btn btn-text"
          lang={l}
          aria-pressed={i18n.language === l}
          onClick={() => choose(l)}
        >
          {t(`language.${l}`)}
        </button>
      ))}
    </div>
  );
}
