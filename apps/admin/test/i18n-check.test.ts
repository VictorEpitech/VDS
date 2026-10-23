import { describe, expect, it } from "vitest";
import { check, checkModule } from "../scripts/i18n-check.mjs";

describe("i18n:check", () => {
  const src = ['t("a.b")', "t(`c.${x}`)", '<Trans i18nKey="d" />'];

  it("passe quand tout est cohérent", () => {
    const fr = { a: { b: "x" }, c: { one: "1" }, d: "d" };
    expect(check({ fr, en: fr, sources: src })).toEqual([]);
  });

  it("détecte clé manquante, inutilisée, inconnue", () => {
    const fr = { a: { b: "x" }, c: { one: "1" }, d: "d", z: "z" };
    const en = { a: { b: "x" }, c: { one: "1" }, d: "d" };
    const errors = check({ fr, en, sources: [...src, 't("nope")'] });
    expect(errors).toEqual(
      expect.arrayContaining(["manquante en EN : z", "utilisée mais absente : nope", "inutilisée : z"]),
    );
  });

  it("compare les modules", () => {
    expect(checkModule({ a: "1" }, { b: "1" })).toHaveLength(2);
  });
});
