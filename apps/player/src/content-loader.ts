import type { ContentTypeEntry, VdsContentElement } from "@vds/sdk-ts";
import { resolveServer } from "./api";

const LOAD_TIMEOUT_MS = 10_000;

/** Charge à la demande le web component d'un content-type (module servi puis mis en cache par le SW). */
export class ContentLoader {
  private loaded = new Map<string, Promise<void>>();

  constructor(private readonly server: string) {}

  ensure(entry: ContentTypeEntry): Promise<void> {
    const key = `${entry.element}@${entry.version}`;
    let p = this.loaded.get(key);
    if (!p) {
      p = this.load(entry);
      this.loaded.set(key, p);
      p.catch(() => this.loaded.delete(key));
    }
    return p;
  }

  private async load(entry: ContentTypeEntry): Promise<void> {
    if (customElements.get(entry.element)) return;
    const url = resolveServer(entry.script, this.server);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        import(/* @vite-ignore */ url),
        new Promise((_, rej) => {
          timer = setTimeout(() => rej(new Error("content_load_timeout")), LOAD_TIMEOUT_MS);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
    if (!customElements.get(entry.element)) throw new Error("content_not_defined");
  }

  create(entry: ContentTypeEntry): VdsContentElement {
    return document.createElement(entry.element) as VdsContentElement;
  }
}
