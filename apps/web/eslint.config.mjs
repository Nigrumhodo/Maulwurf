// D1.1 — ESLint 9 plano para React + Vite. Sustituye `next/core-web-vitals` y
// `next/typescript` del scaffold de Next.js, que ya no aplican (ADR-0007).
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", ".next/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}", "vite.config.ts"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Product source only. Tests keep `javascript:` strings as rejection fixtures.
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "no-restricted-properties": [
        "error",
        {
          object: "document",
          property: "write",
          message: "document.write is an XSS sink.",
        },
        {
          object: "document",
          property: "writeln",
          message: "document.writeln is an XSS sink.",
        },
        {
          property: "innerHTML",
          message: "Assigning innerHTML is an XSS sink.",
        },
        {
          property: "outerHTML",
          message: "Assigning outerHTML is an XSS sink.",
        },
      ],
    },
  },
);
