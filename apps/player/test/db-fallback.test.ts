import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OPEN_TIMEOUT_MS, clearAll, isMemoryOnly, kvDel, kvGet, kvSet, logAdd, resetDbForTests, scheduleGet, schedulePut } from "../src/db";

const g = globalThis as Record<string, unknown>;
let saved: unknown;

beforeEach(() => {
  saved = g.indexedDB;
  resetDbForTests();
});
afterEach(() => {
  g.indexedDB = saved;
  vi.useRealTimers();
});

async function roundTrip(): Promise<void> {
  await kvSet("a", 1);
  expect(await kvGet("a")).toBe(1);
  await kvDel("a");
  expect(await kvGet("a")).toBeUndefined();
  const doc = { revision: "r" } as never;
  await schedulePut(doc);
  expect(await scheduleGet()).toBe(doc);
  await logAdd({ at: 1, code: "x" });
  await clearAll();
  expect(await scheduleGet()).toBeUndefined();
}

describe("db : repli mémoire", () => {
  it("indexedDB absent", async () => {
    g.indexedDB = undefined;
    await roundTrip();
    expect(isMemoryOnly()).toBe(true);
  });

  it("indexedDB.open lève", async () => {
    g.indexedDB = {
      open: () => {
        throw new Error("SecurityError");
      },
    };
    await roundTrip();
    expect(isMemoryOnly()).toBe(true);
  });

  it("indexedDB.open ne répond jamais : timeout puis repli", async () => {
    vi.useFakeTimers();
    g.indexedDB = { open: () => ({}) };
    const p = kvGet("k");
    await vi.advanceTimersByTimeAsync(OPEN_TIMEOUT_MS + 10);
    expect(await p).toBeUndefined();
    expect(isMemoryOnly()).toBe(true);
    await kvSet("k", 2);
    expect(await kvGet("k")).toBe(2);
  });
});
