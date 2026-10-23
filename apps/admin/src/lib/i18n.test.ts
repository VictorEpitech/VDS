import { describe, expect, it } from "vitest";
import { initI18n, pickLocale } from "./i18n";

describe("i18n", () => {
  it("choisit la langue stockée valide, sinon celle de la config", () => {
    expect(pickLocale("fr", "en")).toBe("en");
    expect(pickLocale("fr", "de")).toBe("fr");
    expect(pickLocale("en", null)).toBe("en");
  });

  it("traduit en FR et EN, positionne <html lang> et gère les pluriels ICU", async () => {
    const i18n = await initI18n("fr", null);
    expect(i18n.t("nav.home")).toBe("Accueil");
    expect(document.documentElement.lang).toBe("fr");
    i18n.addResource("fr", "translation", "x.count", "{count, plural, one {# écran} other {# écrans}}");
    expect(i18n.t("x.count", { count: 1 })).toBe("1 écran");
    expect(i18n.t("x.count", { count: 3 })).toBe("3 écrans");
    await i18n.changeLanguage("en");
    expect(i18n.t("nav.home")).toBe("Home");
    expect(document.documentElement.lang).toBe("en");
  });
});
