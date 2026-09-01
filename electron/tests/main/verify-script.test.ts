import { execFile } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const run = promisify(execFile);
const root = resolve(__dirname, "../../..");
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

describe("scripts/test.sh", () => {
  it("builds the desktop bundle before invoking Electron E2E", async () => {
    const directory = await mkdtemp(join(tmpdir(), "docmind-verify-script-"));
    temporaryDirectories.push(directory);
    const bin = join(directory, "bin");
    const log = join(directory, "commands.log");
    await writeFile(
      join(directory, "uv"),
      '#!/usr/bin/env bash\nprintf \'uv %s\\n\' "$*" >> "$DOCMIND_VERIFY_LOG"\n',
      "utf8",
    );
    await writeFile(
      join(directory, "npm"),
      '#!/usr/bin/env bash\nprintf \'npm %s\\n\' "$*" >> "$DOCMIND_VERIFY_LOG"\n',
      "utf8",
    );
    await chmod(join(directory, "uv"), 0o755);
    await chmod(join(directory, "npm"), 0o755);
    await run("mkdir", ["-p", bin]);
    await run("ln", ["-s", join(directory, "uv"), join(bin, "uv")]);
    await run("ln", ["-s", join(directory, "npm"), join(bin, "npm")]);

    await run("bash", ["scripts/test.sh"], {
      cwd: root,
      env: { ...process.env, DOCMIND_VERIFY_LOG: log, PATH: `${bin}:${process.env.PATH}` },
    });

    const commands = (await readFile(log, "utf8")).trim().split("\n");
    expect(commands.indexOf("npm run desktop:build")).toBeGreaterThanOrEqual(0);
    expect(commands.indexOf("npm run desktop:build")).toBeLessThan(
      commands.indexOf("npm run test:e2e"),
    );
  });
});
