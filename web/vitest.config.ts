import { defineConfig } from "vitest/config";

// Logic tests only: pure TypeScript, no Astro/Vite plugins needed.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
