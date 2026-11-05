import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Compatibilité Node 22/24 + JSDOM + React Router 7 :
// Node undici Request valide strictement que signal est une instance d'undici AbortSignal.
// En environnement JSDOM, React Router instancie le AbortController de JSDOM, ce qui fait échouer undici RequestInit.
const OriginalRequest = globalThis.Request;
if (OriginalRequest) {
  (globalThis as unknown as Record<string, unknown>).Request = class Request extends OriginalRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      if (init && init.signal) {
        try {
          super(input, init);
        } catch {
          const { signal: _, ...rest } = init;
          super(input, rest);
        }
      } else {
        super(input, init);
      }
    }
  };
}

afterEach(() => {
  cleanup();
  document.head.querySelectorAll("link[data-vds-theme]").forEach((l) => l.remove());
  window.localStorage.clear();
  delete window.__VDS__;
});
