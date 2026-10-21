// @ts-check
import preact from "@astrojs/preact";
import { defineConfig } from "astro/config";

// GitHub Pages serves the project under /haggler-agent/; both values can be overridden so the
// same build works for a custom domain or a local preview at "/".
const site = process.env.SITE_URL || "https://glrodasz.github.io";
const rawBase = process.env.BASE_PATH ?? "/haggler-agent";
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
