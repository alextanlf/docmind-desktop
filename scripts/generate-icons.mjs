#!/usr/bin/env node
/**
 * DocMind 应用图标生成器 · 「折页星火」
 *
 * 数据源：设计定稿 docmind-app-icon.html
 *   · 画布 48 × 48，圆角 22.5%（rx = 10.8）
 *   · 页身 26 × 34，标准几何线宽 2.8 / 星火外径 6.6
 *   · ≤ 24px 使用小尺寸修正几何：线宽 3.4 / 星火外径 8.0，省略折角线
 *   · 靛蓝单色 #4f46e5，实心版为 Dock / 任务栏 / 安装包主版本
 *
 * 产物：
 *   build/icons/svg/*.svg            矢量主文件（实心 / 浅色 / 深色 / 透明 / 修正 / 自适应）
 *   build/icons/png/*.png            16–1024 像素阶梯（≤24px 自动切换修正几何）
 *   build/icons/DocMind.icns         macOS 应用图标
 *   build/icons/DocMind.ico          Windows 应用图标
 *   electron/renderer/public/*       窗口图标、favicon 与 PWA 图标
 *
 * 用法：node scripts/generate-icons.mjs
 */
import { execFile } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { chromium } from "playwright";

const run = promisify(execFile);
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const iconDir = join(root, "build/icons");
const svgDir = join(iconDir, "svg");
const pngDir = join(iconDir, "png");
const publicDir = join(root, "electron/renderer/public");

/** 设计定稿色板（color-mix 已按 Chromium 计算值落为 sRGB）。 */
const PALETTE = {
  accent: "#4f46e5",
  surface: "#ffffff",
  border: "#e2e4f0",
};
const THEMES = {
  solid: { bg: PALETTE.accent, fg: PALETTE.surface },
  light: { bg: PALETTE.surface, fg: PALETTE.accent, ring: PALETTE.border },
  dark: { bg: "#1d1d45", fg: "#e6eaff", ring: "#25245d" },
  transparent: { bg: null, fg: PALETTE.accent },
};

const PAGE_BODY =
  "M14.5 7H28L37 16V37.5A3.5 3.5 0 0 1 33.5 41H14.5A3.5 3.5 0 0 1 11 37.5V10.5A3.5 3.5 0 0 1 14.5 7Z";

/** 标准几何：完整页身 + 折角线 + 6.6 外径星火。 */
const GEOMETRY = {
  full: [
    `<path d="${PAGE_BODY}" fill="currentColor" fill-opacity="0.14"/>`,
    `<g fill="none" stroke="currentColor" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round">`,
    `<path d="${PAGE_BODY}"/>`,
    `<path d="M28 7V13.5A2.5 2.5 0 0 0 30.5 16H37"/>`,
    `</g>`,
    `<path d="M24 18.4c.78 4.2 2.42 5.84 6.6 6.6-4.18.78-5.82 2.42-6.6 6.6-.78-4.18-2.42-5.82-6.6-6.6 4.18-.76 5.82-2.4 6.6-6.6Z" fill="currentColor"/>`,
  ].join(""),
  compact: [
    `<path d="${PAGE_BODY}" fill="currentColor" fill-opacity="0.16"/>`,
    `<path d="${PAGE_BODY}" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/>`,
    `<path d="M24 16.5c.94 5.09 2.93 7.08 8 8-5.07.94-7.05 2.93-8 8-.94-5.07-2.93-7.05-8-8 5.07-.92 7.05-2.91 8-8Z" fill="currentColor"/>`,
  ].join(""),
};

/** ≤24px 使用修正几何，其余用标准几何。 */
function geometryFor(size) {
  return size <= 24 ? "compact" : "full";
}

function glyph(geometry, color, transform = "") {
  const body = GEOMETRY[geometry].split("currentColor").join(color);
  return transform ? `<g transform="${transform}">${body}</g>` : body;
}

/**
 * 生成独立 SVG。自适应版本铺满底色（交给启动器裁切），标记收进 66/108 安全区。
 */
function buildSvg({ variant = "solid", geometry = "full", size = 1024, maskable = false } = {}) {
  const theme = THEMES[variant];
  const layers = [];
  if (theme.bg) {
    const rx = maskable ? 0 : 10.8;
    layers.push(`<rect width="48" height="48" rx="${rx}" fill="${theme.bg}"/>`);
    if (theme.ring && !maskable) {
      layers.push(
        `<rect x="0.5" y="0.5" width="47" height="47" rx="10.4" fill="none" stroke="${theme.ring}"/>`,
      );
    }
  }
  layers.push(
    glyph(geometry, theme.fg, maskable ? "translate(24 24) scale(0.72) translate(-24 -24)" : ""),
  );
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="${size}" height="${size}" role="img" aria-label="DocMind">`,
    layers.join(""),
    `</svg>`,
  ].join("");
}

/** 在 Chromium 中按目标像素渲染，圆角外保持透明。 */
async function rasterize(page, svg, size) {
  return page.evaluate(
    async ({ markup, pixels }) => {
      const image = new Image();
      image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = pixels;
      canvas.height = pixels;
      const context = canvas.getContext("2d");
      context.clearRect(0, 0, pixels, pixels);
      context.drawImage(image, 0, 0, pixels, pixels);
      const dataUrl = canvas.toDataURL("image/png");
      return {
        png: Uint8Array.from(atob(dataUrl.split(",")[1]), (char) => char.charCodeAt(0)),
        pixels:
          pixels <= 64 ? Array.from(context.getImageData(0, 0, pixels, pixels).data) : undefined,
      };
    },
    { markup: svg, pixels: size },
  );
}

/** 32 位 DIB 图标数据（BMP 条目），保证旧版资源工具也能正确显示透明圆角。 */
function buildDib(pixels, size) {
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(size, 4);
  header.writeInt32LE(size * 2, 8);
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  const maskStride = Math.ceil(size / 32) * 4;
  const imageSize = size * size * 4;
  header.writeUInt32LE(imageSize + maskStride * size, 20);

  const xor = Buffer.alloc(imageSize);
  const and = Buffer.alloc(maskStride * size);
  for (let y = 0; y < size; y += 1) {
    const sourceRow = (size - 1 - y) * size * 4;
    const targetRow = y * size * 4;
    for (let x = 0; x < size; x += 1) {
      const source = sourceRow + x * 4;
      const target = targetRow + x * 4;
      xor[target] = pixels[source + 2];
      xor[target + 1] = pixels[source + 1];
      xor[target + 2] = pixels[source];
      xor[target + 3] = pixels[source + 3];
      if (pixels[source + 3] < 128) {
        and[y * maskStride + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
  }
  return Buffer.concat([header, xor, and]);
}

/** ICO 容器：小尺寸用 DIB，128/256 用 PNG 压缩条目。 */
function buildIco(entries) {
  const directory = Buffer.alloc(6 + entries.length * 16);
  directory.writeUInt16LE(0, 0);
  directory.writeUInt16LE(1, 2);
  directory.writeUInt16LE(entries.length, 4);
  let offset = directory.length;
  entries.forEach((entry, index) => {
    const start = 6 + index * 16;
    directory.writeUInt8(entry.size >= 256 ? 0 : entry.size, start);
    directory.writeUInt8(entry.size >= 256 ? 0 : entry.size, start + 1);
    directory.writeUInt8(0, start + 2);
    directory.writeUInt8(0, start + 3);
    directory.writeUInt16LE(1, start + 4);
    directory.writeUInt16LE(32, start + 6);
    directory.writeUInt32LE(entry.data.length, start + 8);
    directory.writeUInt32LE(offset, start + 12);
    offset += entry.data.length;
  });
  return Buffer.concat([directory, ...entries.map((entry) => entry.data)]);
}

async function main() {
  await mkdir(svgDir, { recursive: true });
  await mkdir(pngDir, { recursive: true });
  await mkdir(publicDir, { recursive: true });

  const sources = {
    "docmind-icon.svg": buildSvg({ variant: "solid", geometry: "full" }),
    "docmind-icon-light.svg": buildSvg({ variant: "light", geometry: "full" }),
    "docmind-icon-dark.svg": buildSvg({ variant: "dark", geometry: "full" }),
    "docmind-icon-transparent.svg": buildSvg({ variant: "transparent", geometry: "full" }),
    "docmind-icon-compact.svg": buildSvg({ variant: "solid", geometry: "compact" }),
    "docmind-icon-maskable.svg": buildSvg({ variant: "solid", geometry: "full", maskable: true }),
  };
  for (const [name, markup] of Object.entries(sources)) {
    await writeFile(join(svgDir, name), `${markup}\n`, "utf8");
  }

  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const png = new Map();
  const dib = new Map();
  const ladder = [16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 256, 384, 512, 1024];
  for (const size of ladder) {
    const markup = buildSvg({ variant: "solid", geometry: geometryFor(size), size });
    const rendered = await rasterize(page, markup, size);
    png.set(size, Buffer.from(rendered.png));
    if (rendered.pixels) dib.set(size, buildDib(rendered.pixels, size));
  }
  const maskable = await rasterize(
    page,
    buildSvg({ variant: "solid", geometry: "full", size: 512, maskable: true }),
    512,
  );
  await browser.close();

  for (const size of ladder) {
    await writeFile(join(pngDir, `docmind-icon-${size}.png`), png.get(size));
  }
  await writeFile(join(pngDir, "docmind-icon-maskable-512.png"), Buffer.from(maskable.png));

  const ico = buildIco(
    [16, 24, 32, 48, 64]
      .map((size) => ({ size, data: dib.get(size) }))
      .concat([128, 256].map((size) => ({ size, data: png.get(size) }))),
  );
  await writeFile(join(iconDir, "DocMind.ico"), ico);
  await writeFile(join(publicDir, "favicon.ico"), ico);
  await writeFile(join(publicDir, "icon.svg"), `${sources["docmind-icon.svg"]}\n`, "utf8");
  await writeFile(join(publicDir, "icon-256.png"), png.get(256));
  await writeFile(join(publicDir, "icon-512.png"), png.get(512));

  const appleTouch = await (async () => {
    const appleBrowser = await chromium.launch();
    const applePage = await appleBrowser.newPage({ deviceScaleFactor: 1 });
    const rendered = await rasterize(
      applePage,
      buildSvg({ variant: "solid", geometry: "full", size: 180 }),
      180,
    );
    await appleBrowser.close();
    return Buffer.from(rendered.png);
  })();
  await writeFile(join(publicDir, "apple-touch-icon.png"), appleTouch);

  const iconset = join(iconDir, "DocMind.iconset");
  await rm(iconset, { recursive: true, force: true });
  await mkdir(iconset, { recursive: true });
  const iconsetFiles = [
    ["icon_16x16.png", 16],
    ["icon_16x16@2x.png", 32],
    ["icon_32x32.png", 32],
    ["icon_32x32@2x.png", 64],
    ["icon_128x128.png", 128],
    ["icon_128x128@2x.png", 256],
    ["icon_256x256.png", 256],
    ["icon_256x256@2x.png", 512],
    ["icon_512x512.png", 512],
    ["icon_512x512@2x.png", 1024],
  ];
  for (const [name, size] of iconsetFiles) {
    await writeFile(join(iconset, name), png.get(size));
  }
  if (process.platform === "darwin" && existsSync("/usr/bin/iconutil")) {
    await run("/usr/bin/iconutil", [
      "-c",
      "icns",
      join(iconset),
      "-o",
      join(iconDir, "DocMind.icns"),
    ]);
    await rm(iconset, { recursive: true, force: true });
  } else {
    console.log("跳过 .icns：仅在 macOS 上可用 iconutil");
  }

  const relative = (target) => target.slice(root.length + 1);
  console.log(
    [
      "图标资源已生成：",
      `  ${relative(svgDir)}/ · 6 个矢量主文件`,
      `  ${relative(pngDir)}/ · ${ladder.length + 1} 个 PNG`,
      `  ${relative(join(iconDir, "DocMind.icns"))}`,
      `  ${relative(join(iconDir, "DocMind.ico"))}`,
      `  ${relative(publicDir)}/ · icon.svg · favicon.ico · apple-touch-icon.png`,
    ].join("\n"),
  );
}

await main();
