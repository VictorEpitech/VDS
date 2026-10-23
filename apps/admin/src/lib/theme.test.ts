import { describe, expect, it } from "vitest";
import { loadStylesheet } from "./theme";

describe("loadStylesheet", () => {
  it("ajoute le lien du thème et se résout au chargement", async () => {
    const p = loadStylesheet("/espace/affichage/modules/theme.allia/assets/theme.css");
    const link = document.head.querySelector<HTMLLinkElement>("link[data-vds-theme]");
    expect(link?.getAttribute("href")).toBe("/espace/affichage/modules/theme.allia/assets/theme.css");
    link?.dispatchEvent(new Event("load"));
    await expect(p).resolves.toBeUndefined();
  });

  it("ne bloque pas si le thème est indisponible", async () => {
    const p = loadStylesheet("/x.css");
    document.head.querySelector("link[data-vds-theme]")?.dispatchEvent(new Event("error"));
    await expect(p).resolves.toBeUndefined();
  });
});
