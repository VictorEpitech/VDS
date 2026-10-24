import { afterEach, describe, expect, it, vi } from "vitest";
import { newRequestId, powerSetArgs } from "./commands";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("identifiant de requête", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fonctionne sans crypto.randomUUID (contexte non sécurisé)", () => {
    vi.stubGlobal("crypto", { getRandomValues: (a: Uint8Array) => a.map((_, i) => i * 7 + 3) });
    expect(newRequestId()).toMatch(UUID);
    expect(powerSetArgs("on")).toMatchObject({ state: "on", reason: "manual" });
  });

  it("produit des identifiants distincts avec randomUUID", () => {
    expect(newRequestId()).not.toBe(newRequestId());
  });
});
