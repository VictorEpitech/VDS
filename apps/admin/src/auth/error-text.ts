import { useTranslation } from "react-i18next";

/** Traduit un code d'erreur de l'API (`errors.<code>`) ; code inconnu : message générique. */
export function useErrorText(): (code: string) => string {
  const { t, i18n } = useTranslation();
  return (code) => {
    const rest = code.startsWith("errors.") ? code.slice("errors.".length) : code;
    return i18n.exists(`errors.${rest}`) ? t(`errors.${rest}`) : t("errors.unknown");
  };
}
