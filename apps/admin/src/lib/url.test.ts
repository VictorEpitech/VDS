import { describe, expect, it } from "vitest";
import { joinBase } from "./url";

describe("joinBase", () => {
  it("racine", () => {
    expect(joinBase("", "/health")).toBe("/health");
    expect(joinBase("", "health")).toBe("/health");
  });
  it("sous-chemin", () => {
    expect(joinBase("/espace/affichage", "/api/v1/x")).toBe("/espace/affichage/api/v1/x");
  });
});
