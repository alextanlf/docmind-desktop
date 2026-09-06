import { defineConfig } from "vitest/config";
import { resolve } from "node:path";
export default defineConfig({
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "node_modules/**", ".worktrees/**"],
    setupFiles: [resolve(__dirname, "electron/tests/setup.ts")],
  },
});
