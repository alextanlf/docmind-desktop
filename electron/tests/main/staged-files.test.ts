import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { mkdtemp, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StagedFileService } from "../../main/staged-files";

describe("staged files", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "docmind-"));
  });
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });
  it("stages dialog-selected file through the same validation path", async () => {
    const src = join(dir, "guide.md");
    await writeFile(src, "hello");
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async () => ({ canceled: false, filePaths: [src] }),
    });
    await expect(service.chooseAndStage("markdown")).resolves.toMatchObject({
      name: "guide.md",
    });
  });
  it("returns null when the file dialog is cancelled", async () => {
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });
    await expect(service.chooseAndStage("pdf")).resolves.toBeNull();
  });
  it("copies approved markdown without exposing original path", async () => {
    const src = join(dir, "guide.md");
    await writeFile(src, "hello");
    const result = await new StagedFileService({ dataDir: dir }).stageSelected(
      src,
      "markdown",
    );
    expect(result).toMatchObject({
      kind: "staged_file",
      name: "guide.md",
      mediaType: "text/markdown",
      sizeBytes: 5,
    });
    expect(result.stagedSourceId).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.stringify(result)).not.toContain(dir);
  });
  it.each([".txt", ".exe", ".html"])(
    "rejects unsupported extension %s",
    async (ext) => {
      await expect(
        new StagedFileService({ dataDir: dir }).stageSelected(
          join(dir, "file" + ext),
          "markdown",
        ),
      ).rejects.toMatchObject({ code: "SOURCE_UNSUPPORTED" });
    },
  );
  it("rejects symlink", async () => {
    const src = join(dir, "a.md");
    const target = join(dir, "target.md");
    await writeFile(target, "x");
    await symlink(target, src);
    await expect(
      new StagedFileService({ dataDir: dir }).stageSelected(src, "markdown"),
    ).rejects.toMatchObject({ code: "SOURCE_UNSUPPORTED" });
  });
  it("uses descriptor-verified regular file staging", async () => {
    const source = join(dir, "safe.md");
    await writeFile(source, "x");
    const result = await new StagedFileService({ dataDir: dir }).stageSelected(
      source,
      "markdown",
    );
    expect(result.name).toBe("safe.md");
  });
});
