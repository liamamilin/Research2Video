// Self-hosted Monaco. By default @monaco-editor/react pulls Monaco (JS, CSS
// and a blob: worker) from jsDelivr, which the console's CSP blocks. Here the
// editor core, the YAML grammar and the base worker are bundled by Vite and
// served from 'self', so no CDN or blob: worker is needed.
import { loader } from "@monaco-editor/react";
// edcore.main = editor + all editor features, without the 80+ languages and
// the TS/JSON/CSS/HTML language services that editor.main drags in.
import * as monaco from "monaco-editor/esm/vs/editor/edcore.main";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

loader.config({ monaco });
