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

function expandResources(value: string, resourcesPath: string): string {
  return value.startsWith(RESOURCES_PLACEHOLDER)
    ? join(resourcesPath, value.slice(RESOURCES_PLACEHOLDER.length + 1))
    : value;
}

export function resolveBackendRuntime(
  env: NodeJS.ProcessEnv,
  options: { packaged: boolean; repoDir: string; dataDir: string; resourcesPath?: string },
): BackendRuntimeContract {
  if (!options.packaged)
    return {
      command: "uv",
      args: ["run", "python", "-m", "app"],
      cwd: join(options.repoDir, "backend"),
      dataDir: options.dataDir,
      port: 18900,
    };
  const rawCommand = env.DOCMIND_BACKEND_COMMAND ?? "";
  const rawCwd = env.DOCMIND_BACKEND_CWD ?? "";
  let args: unknown;
  if (env.DOCMIND_BACKEND_ARGS === undefined) throw new Error("BACKEND_START_FAILED");
  try {
    args = JSON.parse(env.DOCMIND_BACKEND_ARGS);
  } catch {
    throw new Error("BACKEND_START_FAILED");
  }
  if (!Array.isArray(args) || args.some((v) => typeof v !== "string"))
    throw new Error("BACKEND_START_FAILED");

  // 占位符路径按 resourcesPath 展开；其余路径要求绝对路径。
  const command = expandResources(rawCommand, options.resourcesPath ?? "");
  const cwd = expandResources(rawCwd, options.resourcesPath ?? "");
  if (!isAbsolute(command) || !isAbsolute(cwd)) throw new Error("BACKEND_START_FAILED");
  try {
    accessSync(command, constants.X_OK);
    accessSync(cwd, constants.R_OK);
    if (!statSync(command).isFile() || !statSync(cwd).isDirectory())
      throw new Error("invalid path kind");
  } catch {
    throw new Error("BACKEND_START_FAILED");
  }
  return { command, args: args as string[], cwd, dataDir: options.dataDir, port: 18900 };
}
