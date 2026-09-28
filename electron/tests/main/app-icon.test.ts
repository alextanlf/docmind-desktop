import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { resolveIconPath, resolveWindowIcon } from "../../main/window-manager";

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

/** 最小 PNG 解码：生成脚本产出的都是 8 位 RGBA 非隔行 PNG，够用来量透明边距。 */
function decodePng(path: string) {
  const buffer = readFileSync(path);
  const chunks: Buffer[] = [];
  let width = 0;
  let height = 0;
  let offset = 8;
  while (offset + 8 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.subarray(offset + 4, offset + 8).toString("latin1");
    const data = buffer.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      expect([data[8], data[9]]).toEqual([8, 6]);
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
    } else if (type === "IDAT") {
      chunks.push(data);
    } else if (type === "IEND") {
      break;
    }
    offset += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(chunks));
  const stride = width * 4;
  const pixels = Buffer.alloc(height * stride);
  const previous = Buffer.alloc(stride);
  let cursor = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[cursor];
    cursor += 1;
    const line = Buffer.from(raw.subarray(cursor, cursor + stride));
    cursor += stride;
    for (let x = 0; x < stride; x += 1) {
      const left = x >= 4 ? line[x - 4] : 0;
      const up = previous[x];
      const upLeft = x >= 4 ? previous[x - 4] : 0;
      const estimate = left + up - upLeft;
      const distances = [
        Math.abs(estimate - left),
        Math.abs(estimate - up),
        Math.abs(estimate - upLeft),
      ];
      const predictor =
        distances[0] <= distances[1] && distances[0] <= distances[2]
          ? left
          : distances[1] <= distances[2]
            ? up
            : upLeft;
      if (filter === 1) line[x] = (line[x] + left) & 0xff;
      else if (filter === 2) line[x] = (line[x] + up) & 0xff;
      else if (filter === 3) line[x] = (line[x] + ((left + up) >> 1)) & 0xff;
      else if (filter === 4) line[x] = (line[x] + predictor) & 0xff;
    }
    line.copy(pixels, y * stride);
    line.copy(previous);
  }
  return { width, height, pixels };
}

/** 不透明像素的包围盒，用来判断图标有没有占满画布。 */
function alphaBounds(path: string) {
  const { width, height, pixels } = decodePng(path);
  const bounds = { left: width, top: height, right: -1, bottom: -1 };
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (pixels[(y * width + x) * 4 + 3] <= 8) continue;
      bounds.left = Math.min(bounds.left, x);
      bounds.right = Math.max(bounds.right, x);
      bounds.top = Math.min(bounds.top, y);
      bounds.bottom = Math.max(bounds.bottom, y);
    }
  }
  return { ...bounds, width, height };
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
      "docmind-icon-mac.svg",
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

  it("keeps the macOS canvas inside Apple's icon grid", () => {
    const dock = alphaBounds(join(iconDir, "png", "docmind-icon-mac-512.png"));
    const margin = Math.round(dock.width * 0.09);
    expect(dock.left).toBeGreaterThanOrEqual(margin);
    expect(dock.top).toBeGreaterThanOrEqual(margin);
    expect(dock.width - 1 - dock.right).toBeGreaterThanOrEqual(margin);
    expect(dock.height - 1 - dock.bottom).toBeGreaterThanOrEqual(margin);

    const full = alphaBounds(join(iconDir, "png", "docmind-icon-512.png"));
    expect(full.left).toBe(0);
    expect(full.top).toBe(0);
    expect(full.right).toBe(full.width - 1);
    expect(full.bottom).toBe(full.height - 1);
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

  it("uses the macOS canvas icon for the Dock", () => {
    expect(resolveIconPath("darwin")).toMatch(/docmind-icon-mac-512\.png$/);
    expect(resolveIconPath("linux")).toMatch(/docmind-icon-256\.png$/);
  });

  it("only passes a window icon to Windows and Linux", () => {
    expect(resolveWindowIcon("darwin")).toBeUndefined();
    const linuxIcon = resolveWindowIcon("linux");
    expect(linuxIcon).toBeTruthy();
    expect(linuxIcon && existsSync(linuxIcon)).toBe(true);
  });
});
