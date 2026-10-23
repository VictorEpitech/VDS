export function flatten(obj: object, prefix?: string): string[];
export function collectUsage(sources: string[]): { exact: Set<string>; prefixes: Set<string> };
export function check(input: { fr: object; en: object; sources: string[] }): string[];
export function checkModule(fr: object, en: object): string[];
