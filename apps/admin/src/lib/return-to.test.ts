import { describe, expect, it } from "vitest";
import { safeReturnTo, toReturnTo } from "./return-to";

describe("safeReturnTo", () => {
  const base = "/espace/affichage";
  it("accepte un chemin sous le préfixe et le retourne relatif", () => {
    expect(safeReturnTo(base, "/espace/affichage/screens?a=1#x")).toBe("/screens?a=1#x");
    expect(safeReturnTo(base, "/espace/affichage")).toBe("/");
    expect(safeReturnTo(base, "/espace/affichage/")).toBe("/");
  });
  it("refuse hors préfixe, schéma, double slash, retour arrière, antislash", () => {
    for (const bad of [
      "/screens",
      "/espace/affichageX/screens",
      "https://evil.test/espace/affichage/",
      "//evil.test/espace/affichage/",
      "/espace/affichage/../../etc",
      "/espace/affichage/\\evil",
      "/espace/affichage/\nx",
      "",
      null,
      undefined,
    ]) {
      expect(safeReturnTo(base, bad)).toBeNull();
    }
  });
  it("à la racine : tout chemin interne, jamais externe", () => {
    expect(safeReturnTo("", "/screens")).toBe("/screens");
    expect(safeReturnTo("", "//evil.test")).toBeNull();
    expect(safeReturnTo("", "javascript:alert(1)")).toBeNull();
  });
  it("ne renvoie pas vers les écrans d'accès", () => {
    expect(safeReturnTo("", "/login?return_to=/x")).toBe("/");
    expect(safeReturnTo(base, "/espace/affichage/bootstrap")).toBe("/");
  });
  it("toReturnTo préfixe par la base", () => {
    expect(toReturnTo(base, "/screens?a=1")).toBe("/espace/affichage/screens?a=1");
    expect(toReturnTo("", "/")).toBe("/");
  });
});
