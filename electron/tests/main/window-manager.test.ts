import { describe, expect, it, vi } from "vitest";
import {
  buildWindowOptions,
  isAllowedNavigation,
  handleWindowOpen,
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
    expect(
      isAllowedNavigation("http://127.0.0.1:5173", "https://evil.test"),
    ).toBe(false);
  });
  it("blocks lookalike origins and bundled-file path escapes", () => {
    expect(
      isAllowedNavigation("http://127.0.0.1:5173", "http://127.0.0.1:51730"),
    ).toBe(false);
    expect(
      isAllowedNavigation(
        "file:///app/renderer/index.html",
        "file:///app/secret.txt",
      ),
    ).toBe(false);
  });
  it("opens only http(s) external URLs", () => {
    const opened: string[] = [];
    expect(handleWindowOpen("https://example.com", (u) => opened.push(u))).toBe(
      false,
    );
    expect(opened).toEqual(["https://example.com/"]);
    expect(handleWindowOpen("file:///tmp/x", (u) => opened.push(u))).toBe(
      false,
    );
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
});
