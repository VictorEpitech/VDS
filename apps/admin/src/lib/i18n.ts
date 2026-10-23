import type { Locale } from "@vds/sdk-ts";
import i18next, { type i18n } from "i18next";
import { initReactI18next } from "react-i18next";
import { icuFormat } from "./icu";
import en from "../i18n/en.json";
import fr from "../i18n/fr.json";

export const LOCALES: readonly Locale[] = ["fr", "en"];
const STORAGE_KEY = "vds.locale";

export function pickLocale(defaultLocale: Locale, stored: string | null): Locale {
  return LOCALES.find((l) => l === stored) ?? defaultLocale;
}

export function readStoredLocale(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    /* stockage indisponible : le choix vaut pour la session */
  }
}

export async function initI18n(defaultLocale: Locale, stored: string | null = readStoredLocale()): Promise<i18n> {
  const instance = i18next.createInstance();
  await instance
    .use(icuFormat)
    .use(initReactI18next)
    .init({
      lng: pickLocale(defaultLocale, stored),
      fallbackLng: "fr",
      supportedLngs: [...LOCALES],
      resources: { fr: { translation: fr }, en: { translation: en } },
      interpolation: { escapeValue: false },
      returnNull: false,
    });
  document.documentElement.lang = instance.language;
  instance.on("languageChanged", (lng) => {
    document.documentElement.lang = lng;
  });
  return instance;
}
