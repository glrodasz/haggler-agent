#!/usr/bin/env node
// Runs before "astro build": a build without web/src/data would publish an empty site, so fail
// early with instructions instead of an obscure Vite error.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { DATA_FILES } from "./data-files.mjs";

const dataDir = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "data");

const problems = [];
if (!existsSync(dataDir)) {
  problems.push("no existe la carpeta web/src/data/");
} else {
  for (const name of DATA_FILES) {
    const file = join(dataDir, `${name}.json`);
    if (!existsSync(file)) {
      problems.push(`falta web/src/data/${name}.json`);
      continue;
    }
    try {
      JSON.parse(readFileSync(file, "utf8"));
    } catch (err) {
      problems.push(`web/src/data/${name}.json no es JSON válido (${err.message})`);
    }
  }
}

if (problems.length > 0) {
  console.error("\n✖ No se puede construir el sitio: faltan los datos.\n");
  for (const p of problems) console.error(`  - ${p}`);
  console.error(
    [
      "",
      "Genera los datos reales desde la raíz del repositorio:",
      "    uv run haggler site-data --data-dir data --out web/src/data",
      "o usa los datos de ejemplo:",
      "    npm run sample-data",
      "",
    ].join("\n"),
  );
  process.exit(1);
}
console.log(`✔ Datos del sitio presentes en ${dataDir}`);
