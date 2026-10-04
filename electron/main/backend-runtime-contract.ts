import { accessSync, constants, statSync } from "node:fs";
import { isAbsolute, join } from "node:path";

export type BackendRuntimeContract = {
  command: string;
  args: string[];
  cwd: string;
  dataDir: string;
  port: 18900;
};

/**
 * Info.plist 里的后端路径用 `$RESOURCES` 占位符，而不是写死绝对路径。
 *
 * 原因：electron-builder 把 `extendInfo.LSEnvironment` 原样写进 Info.plist，
 * 而 Info.plist 只支持固定字符串，不支持相对路径或运行期变量展开。若在配置里
 * 写死构建机的仓库路径，打出来的包换台机器就找不到后端（历史遗留问题）。
 * `$RESOURCES` 在这里只是一个约定标记，真正解析发生在 packaged 运行时 ——
 * 由 main 进程用 process.resourcesPath 展开成
 * `<app>/Contents/Resources/backend-runtime/...`。
 */
const RESOURCES_PLACEHOLDER = "$RESOURCES";

/** extraResources 里后端 runtime 的目录名（见 electron-builder.config.js）。 */
const RUNTIME_DIR = "backend-runtime";
/** runtime 根目录的入口脚本，由 build-backend-runtime.sh 生成。 */
const RUNTIME_ENTRY_ARGS = ["run.py"];

/**
 * packaged 态解释器在 runtime 目录内的候选相对路径，按平台排序。
 *
 * 为什么需要候选列表而不是单一固定名：uv 托管的独立 CPython 在两个平台上的
 * 目录布局不同，脚本也没法把 Windows 的可执行文件改名成 `bin/python3`
 * （改名后 exe 找不到旁边的 `Lib/` 就废了）。
 *   - POSIX：`bin/python3`（脚本建的符号链接，指向 `python3.12`）
 *   - Windows：`python.exe`（uv 的 install-only 布局，exe 与 `Lib/` 同级）
 * 逐个探测而不是猜，能让同一份 main 进程代码在 mac / Windows / Linux 上都成立。
 */
const RUNTIME_EXECUTABLE_CANDIDATES: Record<string, readonly string[]> = {
  win32: ["python.exe", "python3.exe", join("bin", "python.exe")],
  default: [join("bin", "python3"), join("bin", "python"), "python3"],
};

function expandResources(value: string, resourcesPath: string): string {
  return value.startsWith(RESOURCES_PLACEHOLDER)
    ? join(resourcesPath, value.slice(RESOURCES_PLACEHOLDER.length + 1))
    : value;
}

/** 只接受真实存在、可执行、且是普通文件的命令；cwd 必须是已存在的目录。 */
function isUsableCommand(command: string, cwd: string): boolean {
  if (!isAbsolute(command) || !isAbsolute(cwd)) return false;
  try {
    accessSync(command, constants.X_OK);
    accessSync(cwd, constants.R_OK);
    return statSync(command).isFile() && statSync(cwd).isDirectory();
  } catch {
    return false;
  }
}

/**
 * env 契约：macOS 走 Info.plist 的 `extendInfo.LSEnvironment`，也用于测试与
 * 「临时指向别处」的场景。任一字段缺失/不可用就整体放弃（不做部分推断）。
 */
function resolveFromEnv(env: NodeJS.ProcessEnv, resourcesPath: string) {
  const rawCommand = env.DOCMIND_BACKEND_COMMAND ?? "";
  if (!rawCommand) return undefined;
  // args 缺失时不猜：`-m app` 与 `run.py` 是两套入口，猜错会静默启动失败。
  if (env.DOCMIND_BACKEND_ARGS === undefined) return undefined;
  let args: unknown;
  try {
    args = JSON.parse(env.DOCMIND_BACKEND_ARGS);
  } catch {
    return undefined;
  }
  if (!Array.isArray(args) || args.some((v) => typeof v !== "string")) return undefined;

  // 占位符路径按 resourcesPath 展开；其余路径要求绝对路径。
  const command = expandResources(rawCommand, resourcesPath);
  const cwd = expandResources(env.DOCMIND_BACKEND_CWD ?? "", resourcesPath);
  if (!isUsableCommand(command, cwd)) return undefined;
  return { command, args: args as string[], cwd };
}

/**
 * packaged 态的兜底解析：直接从 `process.resourcesPath` 推导出后端入口，
 * 完全不依赖构建期注入的环境变量。
 *
 * 这条路径是 Windows 能启动的关键：macOS 有 `LSEnvironment` 这个 Info.plist
 * 字段可以塞 `DOCMIND_BACKEND_*`，而 Windows 的 NSIS 安装包没有对应机制
 * （electron-builder 也不会把它翻译成注册表环境变量）。既然 `extraResources`
 * 本身是跨平台的，路径推导就该跨平台，而不是把平台差异藏在构建配置里。
 */
function resolveFromResources(resourcesPath: string | undefined, platform: string) {
  if (!resourcesPath) return undefined;
  const cwd = join(resourcesPath, RUNTIME_DIR);
  const candidates =
    RUNTIME_EXECUTABLE_CANDIDATES[platform] ?? RUNTIME_EXECUTABLE_CANDIDATES.default;
  for (const relative of candidates) {
    const command = join(cwd, relative);
    if (isUsableCommand(command, cwd)) return { command, args: [...RUNTIME_ENTRY_ARGS], cwd };
  }
  return undefined;
}

export function resolveBackendRuntime(
  env: NodeJS.ProcessEnv,
  options: {
    packaged: boolean;
    repoDir: string;
    dataDir: string;
    resourcesPath?: string;
    platform?: string;
  },
): BackendRuntimeContract {
  if (!options.packaged)
    return {
      command: "uv",
      args: ["run", "python", "-m", "app"],
      cwd: join(options.repoDir, "backend"),
      dataDir: options.dataDir,
      port: 18900,
    };
  const resourcesPath = options.resourcesPath ?? "";
  const resolved =
    resolveFromEnv(env, resourcesPath) ??
    resolveFromResources(options.resourcesPath, options.platform ?? "");
  if (!resolved) throw new Error("BACKEND_START_FAILED");
  return { ...resolved, dataDir: options.dataDir, port: 18900 };
}
