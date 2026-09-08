const path = require("node:path");

const rootDir = process.cwd();
const backendCwd = process.env.DOCMIND_BACKEND_CWD || path.join(rootDir, "backend");
const backendCommand =
  process.env.DOCMIND_BACKEND_COMMAND || path.join(backendCwd, ".venv", "bin", "python");
const backendArgs = process.env.DOCMIND_BACKEND_ARGS || '["-m", "app"]';

/** @type {import("electron-builder").Configuration} */
module.exports = {
  appId: "local.docmind",
  productName: "DocMind",
  directories: {
    output: "dist",
  },
  files: ["out/**/*", "package.json", "!**/*.map"],
  electronDist: path.join(rootDir, "build", "electron-dist"),
  asar: true,
  npmRebuild: false,
  mac: {
    category: "public.app-category.productivity",
    target: ["dir"],
    identity: null,
    hardenedRuntime: false,
    gatekeeperAssess: false,
    extendInfo: {
      LSEnvironment: {
        DOCMIND_BACKEND_COMMAND: backendCommand,
        DOCMIND_BACKEND_ARGS: backendArgs,
        DOCMIND_BACKEND_CWD: backendCwd,
      },
    },
  },
};
