import js from "@eslint/js";
import i18next from "eslint-plugin-i18next";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "test-results", "playwright-report", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: { ...reactHooks.configs.recommended.rules },
  },
  {
    // Aucun libellé en dur dans l'UI.
    files: ["src/**/*.tsx"],
    ignores: ["src/**/*.test.tsx"],
    ...i18next.configs["flat/recommended"],
    rules: { "i18next/no-literal-string": ["error", { mode: "jsx-text-only" }] },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.{js,ts}"],
    languageOptions: { globals: { process: "readonly", console: "readonly" } },
  },
);
