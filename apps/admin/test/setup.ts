import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  document.head.querySelectorAll("link[data-vds-theme]").forEach((l) => l.remove());
  window.localStorage.clear();
  delete window.__VDS__;
});
