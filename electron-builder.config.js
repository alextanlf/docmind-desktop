const path = require("node:path");
const fs = require("node:fs");

const rootDir = process.cwd();

// 随应用分发的后端 runtime（backend/scripts/build-backend-runtime.sh 产出）。
// 存在时优先使用包内自包含后端，安装包脱离开发机也能运行；
// 不存在时回退到开发态 venv（本地 `npm run dev` 场景）。
const backendRuntimeStage = path.join(rootDir, "backend", "build", "backend-runtime");

// runtime 里的解释器路径在两个平台不同，且不能互相改名：
//   - POSIX：`bin/python3`（构建脚本建的符号链接）
//   - Windows：`python.exe`（uv 的 install-only 布局，exe 必须与 Lib/ 同级）
// 探测时两个都试，避免在 mac 上构建却把 Windows runtime 判成「不存在」而静默回退 venv。
const BUNDLED_INTERPRETER_CANDIDATES = [
  path.join("bin", "python3"),
  path.join("bin", "python"),
  "python.exe",
  "python3.exe",
];
const hasBundledBackend = BUNDLED_INTERPRETER_CANDIDATES.some((relative) =>
  fs.existsSync(path.join(backendRuntimeStage, relative)),
);

// 打包态的后端启动参数指向 Resources/backend-runtime（见下方 extraResources）。
//
// 这里写的是 macOS 专用的 `extendInfo.LSEnvironment`：只有 Info.plist 能这样注入
// 环境变量，Windows NSIS / Linux AppImage 没有对应机制。
// 所以 Windows/Linux 走的是另一条路 —— main 进程用 process.resourcesPath
// 自行推导（见 electron/main/backend-runtime-contract.ts::resolveFromResources）。
// 下面的值只对 macOS 生效，保留它是为了 macOS 走显式契约这条已被验证过的路径。
const backendCommand = hasBundledBackend
  ? "$RESOURCES/backend-runtime/bin/python3"
  : process.env.DOCMIND_BACKEND_COMMAND || path.join(rootDir, "backend", ".venv", "bin", "python");
const backendCwd = hasBundledBackend
  ? "$RESOURCES/backend-runtime"
  : process.env.DOCMIND_BACKEND_CWD || path.join(rootDir, "backend");
const backendArgs = hasBundledBackend
  ? '["run.py"]'
  : process.env.DOCMIND_BACKEND_ARGS || '["-m", "app"]';

// 随应用分发的内置模型暂存目录（package-local.sh 从 ~/.docmind 暂存到
// build/embedding-models）。存在时复制到 Resources/models，后端经
// DOCMIND_BUNDLED_MODELS_DIR 优先从这里解析 embedding 模型。
const embeddingModelStage = path.join(rootDir, "build", "embedding-models");
const extraResources = [
  ...(fs.existsSync(embeddingModelStage) ? [{ from: embeddingModelStage, to: "models" }] : []),
  ...(hasBundledBackend ? [{ from: backendRuntimeStage, to: "backend-runtime" }] : []),
];

/** @type {import("electron-builder").Configuration} */
module.exports = {
  appId: "local.docmind",
  productName: "DocMind",
  directories: {
    output: "dist",
  },
  files: ["out/**/*", "package.json", "!**/*.map"],
  extraResources,
  electronDist: path.join(rootDir, "build", "electron-dist"),
  asar: true,
  npmRebuild: false,
  mac: {
    icon: "build/icons/DocMind.icns",
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
  win: {
    icon: "build/icons/DocMind.ico",
    // 同时产出安装版与免安装版：内部使用者常常拿不到安装权限。
    target: [
      { target: "nsis", arch: ["x64"] },
      { target: "portable", arch: ["x64"] },
    ],
    // 不做代码签名：与 mac 段 identity: null 的现状一致，未签名时
    // SmartScreen 会拦一次，但不影响 NSIS 安装包正常运行。
    signAndEditExecutable: true,
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    // 产物名带版本与架构，避免和 mac 的产物在 dist/ 里混淆。
    artifactName: "${productName}-${version}-${arch}-setup.${ext}",
  },
  linux: {
    icon: "build/icons/png/docmind-icon-512.png",
    category: "Office",
  },
};
