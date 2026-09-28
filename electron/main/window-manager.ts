import { existsSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
let electronShell: { openExternal: (url: string) => Promise<void> } | undefined;
/** 「折页星火」应用图标：开发态取仓库内的 PNG，打包态取随渲染层进入 asar 的副本。 */
const ICON_CANDIDATES = [
  join(__dirname, "../../build/icons/png/docmind-icon-256.png"),
  join(__dirname, "../renderer/icon-256.png"),
];
/** Dock 用 macOS 画布版（四周留透明边距），否则 App 图标会比 Dock 里的邻居大一截。 */
const DOCK_ICON_CANDIDATES = [
  join(__dirname, "../../build/icons/png/docmind-icon-mac-512.png"),
  ...ICON_CANDIDATES,
];
let BrowserWindowCtor: any;
try {
  const electron = require("electron");
  electronShell = electron.shell;
  BrowserWindowCtor = electron.BrowserWindow;
} catch {
  /* tests may run without Electron binary */
}
export function resolveIconPath(platform: string = process.platform): string | undefined {
  const candidates = platform === "darwin" ? DOCK_ICON_CANDIDATES : ICON_CANDIDATES;
  return candidates.find((candidate) => existsSync(candidate));
}

/** macOS 的 Dock 图标来自应用包内的 .icns；Windows / Linux 才需要窗口图标。 */
export function resolveWindowIcon(platform: string = process.platform): string | undefined {
  return platform === "darwin" ? undefined : resolveIconPath(platform);
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
    icon: resolveWindowIcon(),
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
    once: (event: "did-finish-load" | "did-fail-load", callback: (...args: any[]) => void) => void;
  };
  show: () => void;
}) {
  let failed = false;
  window.webContents.once(
    "did-fail-load",
    (_event: unknown, _code: number, _description: string, _url: string, isMainFrame: boolean) => {
      if (isMainFrame) failed = true;
    },
  );
  window.webContents.once("did-finish-load", () => {
    if (!failed) window.show();
  });
}
export function attachRendererLoadDiagnostics(
  window: {
    webContents: {
      once: (
        event: "did-fail-load",
        callback: (
          event: unknown,
          errorCode: number,
          errorDescription: string,
          _validatedURL: string,
          isMainFrame: boolean,
        ) => void,
      ) => void;
    };
  },
  onFailure: (error: Error) => void,
) {
  window.webContents.once(
    "did-fail-load",
    (_event, errorCode, errorDescription, _validatedURL, isMainFrame) => {
      if (isMainFrame)
        onFailure(new Error(`Renderer failed to load (${errorCode}): ${errorDescription}`));
    },
  );
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
