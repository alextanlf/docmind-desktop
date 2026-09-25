import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveWindowIcon } from "../../main/window-manager";

const require = createRequire(import.meta.url);
const root = resolve(__dirname, "../../..");
const iconDir = join(root, "build/icons");
const builderConfig = require(join(root, "electron-builder.config.js")) as {
  mac: { icon: string };
  win: { icon: string };
  linux: { icon: string };
};

const LADDER = [16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 256, 384, 512, 1024];

function readHeader(path: string, length: number) {
  return readFileSync(path).subarray(0, length);
}

describe("application icon", () => {
  it("ships the 折页星火 vector sources", () => {
    for (const name of [
      "docmind-icon.svg",
      "docmind-icon-light.svg",
      "docmind-icon-dark.svg",
      "docmind-icon-transparent.svg",
      "docmind-icon-compact.svg",
      "docmind-icon-maskable.svg",
    ]) {
      expect(existsSync(join(iconDir, "svg", name))).toBe(true);
    }
  });

  it("ships the 16–1024 pixel ladder", () => {
    for (const size of LADDER) {
      const path = join(iconDir, "png", `docmind-icon-${size}.png`);
      expect(existsSync(path), path).toBe(true);
      expect(readHeader(path, 8)).toEqual(
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      );
    }
  });

  it("packages platform icon files", () => {
    expect(readHeader(join(iconDir, "DocMind.icns"), 4).toString("ascii")).toBe("icns");
    const ico = readHeader(join(iconDir, "DocMind.ico"), 6);
    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);
    expect(ico.readUInt16LE(4)).toBeGreaterThanOrEqual(7);
  });

  it("points electron-builder at the generated icons", () => {
    for (const relative of [
      builderConfig.mac.icon,
      builderConfig.win.icon,
      builderConfig.linux.icon,
    ]) {
      expect(existsSync(join(root, relative)), relative).toBe(true);
    }
  });

  it("only passes a window icon to Windows and Linux", () => {
    expect(resolveWindowIcon("darwin")).toBeUndefined();
    const linuxIcon = resolveWindowIcon("linux");
    expect(linuxIcon).toBeTruthy();
    expect(linuxIcon && existsSync(linuxIcon)).toBe(true);
  });
});
