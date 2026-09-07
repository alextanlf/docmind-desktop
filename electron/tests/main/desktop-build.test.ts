import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const run = promisify(execFile);
const root = resolve(__dirname, "../../..");
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { recursive: true, force: true })),
  );
});

async function makeTree(missing?: string) {
  const directory = await mkdtemp(join(tmpdir(), "docmind-desktop-build-"));
  temporaryDirectories.push(directory);
  for (const relative of ["out/main/index.js", "out/preload/index.js", "out/renderer/index.html"]) {
    if (relative === missing) continue;
    const target = join(directory, relative);
    await mkdir(join(target, ".."), { recursive: true });
    await writeFile(target, "ok", "utf8");
  }
  return directory;
}

describe("desktop build verifier", () => {
  it("reports a missing preload artifact", async () => {
    const tree = await makeTree("out/preload/index.js");
    await expect(
      run("node", ["scripts/verify-desktop-build.mjs", "--root", tree], { cwd: root }),
    ).rejects.toMatchObject({
      stderr: expect.stringContaining("preload 产物缺失"),
    });
  });

  it("accepts complete main, preload, and renderer artifacts", async () => {
    const tree = await makeTree();
    const result = await run("node", ["scripts/verify-desktop-build.mjs", "--root", tree], {
      cwd: root,
    });
    expect(result.stdout).toContain("desktop build verified");
  });

  it("rejects a directory masquerading as a preload bundle", async () => {
    const tree = await makeTree();
    await rm(join(tree, "out/preload/index.js"), { recursive: true, force: true });
    await mkdir(join(tree, "out/preload/index.js"), { recursive: true });
    await expect(
      run("node", ["scripts/verify-desktop-build.mjs", "--root", tree], { cwd: root }),
    ).rejects.toMatchObject({ stderr: expect.stringContaining("preload 产物不可读") });
  });
});
