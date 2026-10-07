import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { mkdtemp, writeFile, symlink, rm, open, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StagedFileService, type ImportFormat } from "../../main/staged-files";

/**
 * Format declarations standing in for what the backend serves.
 *
 * Written out here rather than imported from the main process on purpose: the
 * service must be driven entirely by whatever it is handed, so the test states
 * the input instead of sharing a constant with the implementation.
 */
const MARKDOWN: ImportFormat = {
  name: "markdown",
  label: "Markdown",
  extensions: [".md", ".markdown"],
  mediaType: "text/markdown",
  maxBytes: 20 * 1024 * 1024,
};
const PDF: ImportFormat = {
  name: "pdf",
  label: "PDF",
  extensions: [".pdf"],
  mediaType: "application/pdf",
  maxBytes: 100 * 1024 * 1024,
};
const DOCX: ImportFormat = {
  name: "docx",
  label: "Word 文档",
  extensions: [".docx"],
  mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  maxBytes: 20 * 1024 * 1024,
};

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
    await expect(service.chooseAndStage(MARKDOWN)).resolves.toMatchObject({
      name: "guide.md",
    });
  });
  it("returns null when the file dialog is cancelled", async () => {
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });
    await expect(service.chooseAndStage(PDF)).resolves.toBeNull();
  });
  it("offers the declared label and bare extensions to the dialog", async () => {
    let received: unknown;
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async (options) => {
        received = options;
        return { canceled: true, filePaths: [] };
      },
    });

    await service.chooseAndStage(DOCX);

    // The backend declares leading-dot extensions; the dialog wants them bare.
    expect(received).toEqual({
      properties: ["openFile"],
      filters: [{ name: "Word 文档", extensions: ["docx"] }],
    });
  });
  it("copies approved markdown without exposing original path", async () => {
    const src = join(dir, "guide.md");
    await writeFile(src, "hello");
    const result = await new StagedFileService({ dataDir: dir }).stageSelected(src, MARKDOWN);
    expect(result).toMatchObject({
      kind: "staged_file",
      name: "guide.md",
      mediaType: "text/markdown",
      sizeBytes: 5,
    });
    expect(result.stagedSourceId).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.stringify(result)).not.toContain(dir);
  });
  it("stages a docx under the media type its format declares", async () => {
    const src = join(dir, "report.docx");
    await writeFile(src, "PK\u0003\u0004zip");
    const result = await new StagedFileService({ dataDir: dir }).stageSelected(src, DOCX);
    expect(result).toMatchObject({ name: "report.docx", mediaType: DOCX.mediaType });
  });
  it.each([".txt", ".exe", ".html"])("rejects unsupported extension %s", async (ext) => {
    await expect(
      new StagedFileService({ dataDir: dir }).stageSelected(join(dir, "file" + ext), MARKDOWN),
    ).rejects.toMatchObject({ code: "SOURCE_UNSUPPORTED" });
  });
  it("rejects a file whose extension belongs to a different format", async () => {
    const src = join(dir, "report.docx");
    await writeFile(src, "PK\u0003\u0004zip");
    await expect(
      new StagedFileService({ dataDir: dir }).stageSelected(src, MARKDOWN),
    ).rejects.toMatchObject({ code: "SOURCE_UNSUPPORTED" });
  });
  it("enforces the ceiling the format declares, not a fixed one", async () => {
    const src = join(dir, "small.md");
    await writeFile(src, "12345");
    const tiny = { ...MARKDOWN, maxBytes: 4 };
    await expect(
      new StagedFileService({ dataDir: dir }).stageSelected(src, tiny),
    ).rejects.toMatchObject({ code: "SOURCE_TOO_LARGE" });
  });
  it("rejects symlink", async () => {
    const src = join(dir, "a.md");
    const target = join(dir, "target.md");
    await writeFile(target, "x");
    await symlink(target, src);
    await expect(
      new StagedFileService({ dataDir: dir }).stageSelected(src, MARKDOWN),
    ).rejects.toMatchObject({ code: "SOURCE_UNSUPPORTED" });
  });
  it("uses descriptor-verified regular file staging", async () => {
    const source = join(dir, "safe.md");
    await writeFile(source, "x");
    const result = await new StagedFileService({ dataDir: dir }).stageSelected(source, MARKDOWN);
    expect(result.name).toBe("safe.md");
  });

  it("retries short destination writes before atomically publishing the staged file", async () => {
    const source = join(dir, "short-write.md");
    const contents = "deterministic short write regression";
    await writeFile(source, contents);

    const probe = await open(source, "r");
    const fileHandlePrototype = Object.getPrototypeOf(probe) as {
      write: (...args: any[]) => Promise<{ bytesWritten: number }>;
    };
    await probe.close();
    const originalWrite = fileHandlePrototype.write;
    let shortWritePending = true;
    fileHandlePrototype.write = async function (...args: any[]) {
      const [buffer, offset, length, position] = args;
      if (shortWritePending && length > 1) {
        shortWritePending = false;
        const shortLength = Math.max(1, Math.floor(length / 2));
        return originalWrite.call(this, buffer, offset, shortLength, position);
      }
      return originalWrite.apply(this, args);
    };

    try {
      await new StagedFileService({ dataDir: dir }).stageSelected(source, MARKDOWN);
    } finally {
      fileHandlePrototype.write = originalWrite;
    }

    const stagedEntries = await readdir(join(dir, "imports", "staging"));
    const stagedFile = stagedEntries.find((entry) => !entry.endsWith(".partial"));
    expect(stagedFile).toBeDefined();
    await expect(readFile(join(dir, "imports", "staging", stagedFile!))).resolves.toEqual(
      Buffer.from(contents),
    );
  });
});
