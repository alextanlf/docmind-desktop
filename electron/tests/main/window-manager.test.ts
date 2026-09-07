import { describe, expect, it, vi } from "vitest";
import {
  buildWindowOptions,
  isAllowedNavigation,
  handleWindowOpen,
  attachRendererLoadDiagnostics,
  showAfterDidFinishLoad,
} from "../../main/window-manager";

describe("window security", () => {
  it("creates sandboxed renderer without node access", () =>
    expect(buildWindowOptions().webPreferences).toMatchObject({
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    }));
  it("blocks navigation away from app origin", () => {
    expect(isAllowedNavigation("http://127.0.0.1:5173", "https://evil.test")).toBe(false);
  });
  it("blocks lookalike origins and bundled-file path escapes", () => {
    expect(isAllowedNavigation("http://127.0.0.1:5173", "http://127.0.0.1:51730")).toBe(false);
    expect(isAllowedNavigation("file:///app/renderer/index.html", "file:///app/secret.txt")).toBe(
      false,
    );
  });
  it("opens only http(s) external URLs", () => {
    const opened: string[] = [];
    expect(handleWindowOpen("https://example.com", (u) => opened.push(u))).toBe(false);
    expect(opened).toEqual(["https://example.com/"]);
    expect(handleWindowOpen("file:///tmp/x", (u) => opened.push(u))).toBe(false);
    expect(opened).toHaveLength(1);
  });
  it("shows the window only after renderer finish load", () => {
    let callback: (() => void) | undefined;
    const show = vi.fn();
    showAfterDidFinishLoad({
      webContents: {
        once: (_: string, cb: () => void) => {
          callback = cb;
        },
      },
      show,
    } as any);
    expect(show).not.toHaveBeenCalled();
    callback?.();
    expect(show).toHaveBeenCalledOnce();
  });
  it("reports main-frame renderer load failures without exposing the URL", () => {
    let callback:
      | ((
          event: unknown,
          code: number,
          description: string,
          url: string,
          mainFrame: boolean,
        ) => void)
      | undefined;
    const onFailure = vi.fn();
    attachRendererLoadDiagnostics(
      { webContents: { once: (_event: string, cb: typeof callback) => (callback = cb) } } as any,
      onFailure,
    );
    callback?.({}, -2, "ERR_FAILED", "file:///Users/secret/index.html", true);
    expect(onFailure).toHaveBeenCalledWith(
      expect.objectContaining({ message: "Renderer failed to load (-2): ERR_FAILED" }),
    );
    callback?.({}, -2, "ERR_FAILED", "file:///Users/secret/index.html", false);
    expect(onFailure).toHaveBeenCalledTimes(1);
  });
  it("never shows a window after a main-frame load failure", () => {
    const callbacks: Record<string, Array<(...args: any[]) => void>> = {};
    const show = vi.fn();
    showAfterDidFinishLoad({
      webContents: {
        once: (event: string, cb: (...args: any[]) => void) => {
          (callbacks[event] ??= []).push(cb);
        },
      },
      show,
    } as any);
    callbacks["did-fail-load"]?.[0]?.({}, -2, "ERR_FAILED", "file:///secret", true);
    callbacks["did-finish-load"]?.[0]?.();
    expect(show).not.toHaveBeenCalled();
  });
});
