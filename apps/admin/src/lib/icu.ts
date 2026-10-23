import { IntlMessageFormat } from "intl-messageformat";
import type { Module } from "i18next";

/** Module i18nFormat minimal : pluriels et formats ICU (`{count, plural, one {#} other {#}}`) via intl-messageformat. */
const cache = new Map<string, IntlMessageFormat>();

export const icuFormat = {
  type: "i18nFormat",
  init() {},
  parse(res: unknown, options: Record<string, unknown> | undefined, lng: string) {
    if (typeof res !== "string") return res;
    const key = `${lng}\u0000${res}`;
    let fmt = cache.get(key);
    if (!fmt) {
      fmt = new IntlMessageFormat(res, lng, undefined, { ignoreTag: true });
      cache.set(key, fmt);
    }
    return fmt.format((options ?? {}) as Record<string, string | number | Date>);
  },
} as unknown as Module;
