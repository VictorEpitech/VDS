import { useTranslation } from "react-i18next";

/** Traduit un code d'erreur de l'API (`errors.<code>`) ; code inconnu : message générique. */
export function useErrorText(): (code: string, params?: Record<string, unknown>) => string {
  const { t, i18n } = useTranslation();
  return (code, params) => {
    const rest = code.startsWith("errors.") ? code.slice("errors.".length) : code;
    return typeof i18n.getResource(i18n.language, "translation", `errors.${rest}`) === "string" ? t(`errors.${rest}`, params) : t("errors.unknown");
  };
}
