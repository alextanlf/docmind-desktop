import { defineConfig } from "electron-vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  main: {
    build: {
      rollupOptions: { input: resolve(__dirname, "electron/main/index.ts") },
    },
  },
  preload: {
    build: {
      externalizeDeps: false,
      rollupOptions: { input: resolve(__dirname, "electron/preload/index.ts") },
    },
  },
  renderer: {
    root: resolve(__dirname, "electron/renderer"),
    plugins: [react()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "electron/renderer/index.html"),
      },
    },
  },
});
