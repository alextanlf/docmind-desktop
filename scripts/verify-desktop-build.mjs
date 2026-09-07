#!/usr/bin/env node
import { accessSync, constants, existsSync, statSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";

function fail(message) {
  console.error(message);
  process.exitCode = 1;
}

const rootArgument = process.argv.indexOf("--root");
const root = rootArgument >= 0 ? process.argv[rootArgument + 1] : resolve(import.meta.dirname, "..");
if (!root || !isAbsolute(root)) {
  fail("构建产物校验失败：root 必须是绝对路径");
} else {
  const required = [
    ["main", "out/main/index.js"],
    ["preload", "out/preload/index.js"],
    ["renderer", "out/renderer/index.html"],
  ];
  const missing = required.find(([, relative]) => !existsSync(join(root, relative)));
  if (missing) {
    fail(`${missing[0]} 产物缺失：${missing[1]}`);
  } else {
    try {
      const invalid = required.find(([, relative]) => !statSync(join(root, relative)).isFile());
      if (invalid) {
        fail(`${invalid[0]} 产物不可读：${invalid[1]}`);
      } else {
      accessSync(join(root, "out/main/index.js"), constants.R_OK);
      accessSync(join(root, "out/preload/index.js"), constants.R_OK);
      accessSync(join(root, "out/renderer/index.html"), constants.R_OK);
      const rendererUrl = `file://${resolve(root, "out/renderer/index.html")}`;
      if (!rendererUrl.startsWith("file://")) throw new Error("renderer URL");
      console.log("desktop build verified");
      }
    } catch {
      fail("构建产物校验失败：产物不可读");
    }
  }
}
