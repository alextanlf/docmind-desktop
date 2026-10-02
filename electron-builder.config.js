const path = require("node:path");
const fs = require("node:fs");

const rootDir = process.cwd();

// 随应用分发的后端 runtime（backend/scripts/build-backend-runtime.sh 产出）。
// 存在时优先使用包内自包含后端，安装包脱离开发机也能运行；
// 不存在时回退到开发态 venv（本地 `npm run dev` 场景）。
const backendRuntimeStage = path.join(rootDir, "backend", "build", "backend-runtime");
const hasBundledBackend = fs.existsSync(path.join(backendRuntimeStage, "bin", "python3"));

// 打包态的后端启动参数指向 Resources/backend-runtime（见下方 extraResources）。
// Info.plist 里写的是运行时占位符 $RESOURCES，由 backend-manager 在
// packaged 模式下用 process.resourcesPath 解析 —— Info.plist 不支持
// 相对路径或环境变量展开，所以不能在这里写死绝对路径。
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
  },
  linux: {
    icon: "build/icons/png/docmind-icon-512.png",
    category: "Office",
  },
};
