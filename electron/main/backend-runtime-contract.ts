import { accessSync, constants, statSync } from "node:fs";
import { isAbsolute, join } from "node:path";

export type BackendRuntimeContract = {
  command: string;
  args: string[];
  cwd: string;
  dataDir: string;
  port: 18900;
};
export function resolveBackendRuntime(
  env: NodeJS.ProcessEnv,
  options: { packaged: boolean; repoDir: string; dataDir: string },
): BackendRuntimeContract {
  if (!options.packaged)
    return {
      command: "uv",
      args: ["run", "python", "-m", "app"],
      cwd: join(options.repoDir, "backend"),
      dataDir: options.dataDir,
      port: 18900,
    };
  const command = env.DOCMIND_BACKEND_COMMAND ?? "";
  const cwd = env.DOCMIND_BACKEND_CWD ?? "";
  let args: unknown;
  if (env.DOCMIND_BACKEND_ARGS === undefined) throw new Error("BACKEND_START_FAILED");
  try {
    args = JSON.parse(env.DOCMIND_BACKEND_ARGS);
  } catch {
    throw new Error("BACKEND_START_FAILED");
  }
  if (
    !isAbsolute(command) ||
    !isAbsolute(cwd) ||
    !Array.isArray(args) ||
    args.some((v) => typeof v !== "string")
  )
    throw new Error("BACKEND_START_FAILED");
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
