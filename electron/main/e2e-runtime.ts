import { readFile } from "node:fs/promises";
import { join } from "node:path";

export function isE2ERuntime(env: NodeJS.ProcessEnv, isPackaged: boolean): boolean {
  return env.DOCMIND_E2E === "1" && env.DOCMIND_FAKE_SERVICES === "1" && !isPackaged;
}

export async function readE2EDialogPath(dataDir: string): Promise<string | undefined> {
  const path = await readFile(join(dataDir, "e2e", "dialog-path"), "utf8").catch(() => "");
  return path.trim() || undefined;
}
