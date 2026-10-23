import type { Locale } from "@vds/sdk-ts";
import en from "./en.json";
import fr from "./fr.json";

const catalogs: Record<Locale, unknown> = { fr, en };
let current: Locale = "fr";

export function setLocale(value: string | undefined): Locale {
  current = value === "en" ? "en" : "fr";
  document.documentElement.lang = current;
  return current;
}

export function getLocale(): Locale {
  return current;
}

export function t(key: string, locale: Locale = current): string {
  let node: unknown = catalogs[locale];
  for (const part of key.split(".")) {
    if (node && typeof node === "object") node = (node as Record<string, unknown>)[part];
    else return key;
  }
  return typeof node === "string" ? node : key;
}
