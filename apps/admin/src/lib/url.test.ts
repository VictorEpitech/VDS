import { describe, expect, it } from "vitest";
import { joinBase } from "./url";

describe("joinBase", () => {
  it("racine", () => {
    expect(joinBase("", "/health")).toBe("/health");
    expect(joinBase("", "health")).toBe("/health");
  });
  it("base \"/\" (VDS_BASE_PATH=/) : pas de double slash", () => {
    expect(joinBase("/", "media/abc/thumb")).toBe("/media/abc/thumb");
    expect(joinBase("/", "/api/v1/x")).toBe("/api/v1/x");
  });
  it("sous-chemin", () => {
    expect(joinBase("/espace/affichage", "/api/v1/x")).toBe("/espace/affichage/api/v1/x");
  });
});
