/**
 * boot.ts bundled into one self-contained IIFE at build time, so the redirect runs from an inline
 * <script> before anything renders and still uses the same core functions as the rest of the site.
 */
import { join } from "node:path";
import { build } from "rolldown";

let cached: Promise<string> | undefined;

export function localeBootScript(): Promise<string> {
  cached ??= build({
    input: join(process.cwd(), "src/i18n/boot.ts"),
    write: false,
    output: { format: "iife", minify: true },
  }).then((result) => result.output[0].code);
  return cached;
}
