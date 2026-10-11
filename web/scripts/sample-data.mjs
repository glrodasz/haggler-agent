#!/usr/bin/env node
// Copies the illustrative dataset (src/data-sample) into src/data so the site can be built and
// developed without running the Python pipeline. Refuses to overwrite real generated data
// unless --force is given.
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { DATA_FILES } from "./data-files.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src", "data-sample");
const dst = join(root, "src", "data");
const force = process.argv.includes("--force");

const metaPath = join(dst, "meta.json");
if (existsSync(metaPath) && !force) {
  try {
    const meta = JSON.parse(readFileSync(metaPath, "utf8"));
    if (typeof meta.run_id === "string" && !meta.run_id.startsWith("sample")) {
      console.error(
        `✖ web/src/data contiene datos reales (run_id ${meta.run_id}). Usa "npm run sample-data -- --force" para reemplazarlos.`,
      );
      process.exit(1);
    }
  } catch {
    // Unreadable meta.json: treat as disposable.
  }
}

mkdirSync(dst, { recursive: true });
for (const name of DATA_FILES) {
  const from = join(src, `${name}.json`);
  if (!existsSync(from)) {
    console.error(`✖ Falta ${from}`);
    process.exit(1);
  }
  copyFileSync(from, join(dst, `${name}.json`));
}
console.log(`✔ Datos de ejemplo copiados a ${dst} (${DATA_FILES.length} archivos).`);
