/// <reference types="vite/client" />

// edcore.main ships no .d.ts; it exports the same API as editor.api.
declare module "monaco-editor/esm/vs/editor/edcore.main" {
  export * from "monaco-editor/esm/vs/editor/editor.api";
}
declare module "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution";
