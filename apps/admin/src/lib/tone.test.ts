import { afterEach, describe, expect, it, vi } from "vitest";
import { readTone } from "./tone";

afterEach(() => vi.restoreAllMocks());

describe("ton du module de thème", () => {
  it.each([
    ['"formal"', "formal"],
    ["formal", "formal"],
    [" neutral ", "neutral"],
    ["", "neutral"],
    ["autre", "neutral"],
  ])("jeton %j donne %s", (raw, tone) => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({ getPropertyValue: () => raw } as unknown as CSSStyleDeclaration);
    expect(readTone()).toBe(tone);
  });
});
