import { mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";

export type SmokeStatus = "PASS" | "FAIL" | "NOT RUN";
export type LocalRuntimeSmokeRecord = {
  mode: "dev" | "packaged";
  dataDirKind: "temporary";
  health: SmokeStatus;
  rendererLoaded: SmokeStatus;
  backendExited: SmokeStatus;
  portReleased: SmokeStatus;
};

export type TemporaryRuntimeDataDir = {
  kind: "temporary";
  path: string;
  cleanup: () => Promise<void>;
};

export function resolveElectronExecutable(
  env: NodeJS.ProcessEnv,
  exists: (path: string) => boolean = existsSync,
): string {
  const executable = env.DOCMIND_ELECTRON_PATH ?? "";
  if (!isAbsolute(executable) || !exists(executable)) {
    throw new Error("ELECTRON_RUNTIME_REQUIRED");
  }
  return executable;
}

export async function createTemporaryRuntimeDataDir(): Promise<TemporaryRuntimeDataDir> {
  const path = await mkdtemp(join(tmpdir(), "docmind-runtime-"));
  let cleaned = false;
  return {
    kind: "temporary",
    path,
    cleanup: async () => {
      if (cleaned) return;
      cleaned = true;
      await rm(path, { force: true, recursive: true });
    },
  };
}

export function recordLocalRuntimeSmoke(
  input: Omit<LocalRuntimeSmokeRecord, "dataDirKind">,
): LocalRuntimeSmokeRecord {
  return { ...input, dataDirKind: "temporary" };
}
