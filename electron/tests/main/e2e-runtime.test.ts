import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { isE2ERuntime, readE2EDialogPath } from "../../main/e2e-runtime";

const directories: string[] = [];

afterEach(async () => {
  await Promise.all(
    directories.splice(0).map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

describe("isE2ERuntime", () => {
  it("allows controls only for an unpackaged fake-services run", () => {
    expect(
      isE2ERuntime({ DOCMIND_E2E: "1", DOCMIND_FAKE_SERVICES: "1" } as NodeJS.ProcessEnv, false),
    ).toBe(true);
  });

  it.each([
    [{ DOCMIND_E2E: "1" }, false],
    [{ DOCMIND_FAKE_SERVICES: "1" }, false],
    [{ DOCMIND_E2E: "1", DOCMIND_FAKE_SERVICES: "1" }, true],
  ])("rejects test controls outside the permitted runtime (%o)", (env, isPackaged) => {
    expect(isE2ERuntime(env, isPackaged)).toBe(false);
  });

  it("reads the test dialog fixture at each selection", async () => {
    const directory = await mkdtemp(join(tmpdir(), "docmind-e2e-dialog-"));
    directories.push(directory);
    const control = join(directory, "e2e", "dialog-path");
    await mkdir(join(directory, "e2e"), { recursive: true });
    await writeFile(control, "/fixtures/first.md", "utf8");
    expect(await readE2EDialogPath(directory)).toBe("/fixtures/first.md");
    await writeFile(control, "/fixtures/second.md", "utf8");
    expect(await readE2EDialogPath(directory)).toBe("/fixtures/second.md");
  });
});
