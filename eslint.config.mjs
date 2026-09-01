import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["electron/**/*.ts", "e2e/**/*.ts"],
    languageOptions: { globals: globals.node },
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
  { ignores: ["out/**", "node_modules/**"] },
);
