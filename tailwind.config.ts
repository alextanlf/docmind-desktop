import type { Config } from "tailwindcss";

export default {
  content: ["./electron/renderer/index.html", "./electron/renderer/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        workspace: "#f4f5f7",
        surface: "#ffffff",
        ink: "#181b20",
        muted: "#68707d",
        line: "#dfe3e8",
        action: "#2563eb",
        success: "#16803a",
        pending: "#a15c00",
        danger: "#c52a2a",
      },
    },
  },
  plugins: [],
} satisfies Config;
