import { join } from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
let electronShell: { openExternal: (url: string) => Promise<void> } | undefined;
let BrowserWindowCtor: any;
try {
  const electron = require("electron");
  electronShell = electron.shell;
  BrowserWindowCtor = electron.BrowserWindow;
} catch {
  /* tests may run without Electron binary */
}
export function buildWindowOptions(
  preload = join(__dirname, "../preload/index.js"),
): Electron.BrowserWindowConstructorOptions {
  return {
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload,
    },
  };
}
export function isAllowedNavigation(origin: string, target: string) {
  try {
    const allowed = new URL(origin);
    const requested = new URL(target);
    if (allowed.protocol !== requested.protocol) return false;
    if (allowed.protocol === "file:") return allowed.pathname === requested.pathname;
    return (
      allowed.hostname === requested.hostname &&
      allowed.port === requested.port &&
      allowed.protocol === requested.protocol
    );
  } catch {
    return false;
  }
}
export function handleWindowOpen(
  url: string,
  openExternal: (url: string) => void = (u) => {
    void electronShell?.openExternal(u);
  },
) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:")
      openExternal(parsed.toString());
  } catch {
    return false;
  }
  return false;
}
export function showAfterDidFinishLoad(window: {
  webContents: {
    once: (event: "did-finish-load", callback: () => void) => void;
  };
  show: () => void;
}) {
  window.webContents.once("did-finish-load", () => window.show());
}
export function createWindow(origin: string) {
  if (!BrowserWindowCtor) throw new Error("Electron unavailable");
  const win = new BrowserWindowCtor(buildWindowOptions());
  win.webContents.setWindowOpenHandler(({ url }: { url: string }) => {
    handleWindowOpen(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e: any, url: string) => {
    if (!isAllowedNavigation(origin, url)) e.preventDefault();
  });
  return win;
}
