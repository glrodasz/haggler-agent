// @ts-check
import preact from "@astrojs/preact";
import { defineConfig } from "astro/config";

// GitHub Pages serves the site at the root of its custom domain; both values can be overridden
// (publish.yml passes what actions/configure-pages reports) so the same build works elsewhere.
const site = process.env.SITE_URL || "https://haggler.guillermorodas.com";
const rawBase = process.env.BASE_PATH ?? "/";
const base = rawBase === "" ? "/" : rawBase;

export default defineConfig({
  site,
  base,
  output: "static",
  // Directory output ("/services/netflix/index.html") + trailing slashes matches GitHub Pages.
  trailingSlash: "always",
  build: { format: "directory" },
  integrations: [preact()],
  devToolbar: { enabled: false },
});
