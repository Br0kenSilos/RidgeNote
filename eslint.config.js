import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["core/static/core/dist/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["core/static/core/src/**/*.ts", "vite.config.ts"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname
      }
    }
  }
);
