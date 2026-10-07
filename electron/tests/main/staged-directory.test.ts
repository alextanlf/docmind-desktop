import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, extname, join } from "node:path";
import { StagedFileService, stageDirectoryForTest } from "../../main/staged-files";

/**
 * Format declarations standing in for what the backend serves.
 *
 * The directory scanner decides what to copy and how to label it entirely from
 * this list, so the test names the formats rather than relying on any built-in
 * set. Note the ceilings are per format — there is no "pdf limit" and "text
 * limit" in the implementation any more.
 */
const FORMATS = [
  {
    name: "pdf",
    label: "PDF",
    extensions: [".pdf"],
    mediaType: "application/pdf",
    maxBytes: 100 * 1024 * 1024,
  },
  {
    name: "markdown",
    label: "Markdown",
    extensions: [".md", ".markdown"],
    mediaType: "text/markdown",
    maxBytes: 20 * 1024 * 1024,
  },
  {
    name: "docx",
    label: "Word 文档",
    extensions: [".docx"],
    mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    maxBytes: 20 * 1024 * 1024,
  },
  {
    name: "html",
    label: "网页",
    extensions: [".html", ".htm"],
    mediaType: "text/html",
    maxBytes: 20 * 1024 * 1024,
  },
];

type StagingManifest = {
  rootId: string;
  files: Array<{
    relativePath: string;
    stagedId: string;
    mediaType: string;
    sizeBytes: number;
    sha256: string;
  }>;
};

async function readManifest(dataDir: string, collectionId: string): Promise<StagingManifest> {
  return JSON.parse(
    await readFile(
      join(dataDir, "imports", "staging", "collections", collectionId, "manifest.json"),
      "utf8",
    ),
  ) as StagingManifest;
}

describe("staged directories", () => {
  let workspace: string;
  let dataDir: string;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "docmind-directory-"));
    dataDir = join(workspace, "data");
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  it("stages supported regular files without exposing the selected path", async () => {
    const fixtureRoot = join(workspace, "fixture");
    await mkdir(join(fixtureRoot, "docs"), { recursive: true });
    await writeFile(join(fixtureRoot, "guide.pdf"), "%PDF-1.7\n");
    await writeFile(join(fixtureRoot, "docs", "readme.md"), "# Read me\n");
    await writeFile(join(fixtureRoot, "index.html"), "<h1>Index</h1>\n");
    await writeFile(join(fixtureRoot, "ignored.txt"), "not supported\n");

    const result = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir, formats: FORMATS });

    expect(result).toMatchObject({
      displayName: "fixture",
      itemCount: 3,
      totalBytes: 34,
    });
    expect(JSON.stringify(result)).not.toContain(fixtureRoot);
    const manifest = await readManifest(dataDir, result.collectionId);
    expect(manifest.files.every((entry) => !entry.relativePath.startsWith("/"))).toBe(true);
    expect(manifest.files.map((entry) => entry.relativePath)).toEqual([
      "docs/readme.md",
      "guide.pdf",
      "index.html",
    ]);
    expect(manifest.files.map((entry) => entry.mediaType)).toEqual(
      expect.arrayContaining(["application/pdf", "text/markdown", "text/html"]),
    );
    expect(manifest.files.every((entry) => /^[0-9a-f]{64}$/.test(entry.sha256))).toBe(true);
    expect(
      manifest.files.every(
        (entry) =>
          !entry.relativePath.includes("..") &&
          !entry.stagedId.includes("/") &&
          !JSON.stringify(entry).includes(fixtureRoot),
      ),
    ).toBe(true);
  });

  it("opens only a native directory picker and returns null when it is cancelled", async () => {
    let receivedOptions: unknown;
    const service = new StagedFileService({
      dataDir,
      showOpenDialog: async (options) => {
        receivedOptions = options;
        return { canceled: true, filePaths: [] };
      },
    });

    await expect(service.stageDirectory(FORMATS)).resolves.toBeNull();
    expect(receivedOptions).toEqual({ properties: ["openDirectory"] });
  });

  it("skips symlinks and rejects the 1001st supported file", async () => {
    const tooManyFiles = join(workspace, "too-many");
    await mkdir(tooManyFiles);
    await Promise.all(
      Array.from({ length: 1_001 }, (_, index) =>
        writeFile(join(tooManyFiles, `${String(index).padStart(4, "0")}.md`), "x"),
      ),
    );
    await expect(
      stageDirectoryForTest({ selectedPath: tooManyFiles, dataDir, formats: FORMATS }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const symlinkFixture = join(workspace, "links");
    await mkdir(symlinkFixture);
    await writeFile(join(symlinkFixture, "regular.md"), "safe");
    await writeFile(join(workspace, "outside.md"), "outside");
    await symlink(join(workspace, "outside.md"), join(symlinkFixture, "linked.md"));
    await symlink(workspace, join(symlinkFixture, "linked-directory"));
    await expect(
      stageDirectoryForTest({ selectedPath: symlinkFixture, dataDir, formats: FORMATS }),
    ).resolves.toMatchObject({ itemCount: 1 });
  });

  it("reuses an opaque root id and protects its path registry with mode 0600", async () => {
    const fixtureRoot = join(workspace, "stable-root");
    await mkdir(fixtureRoot);
    await writeFile(join(fixtureRoot, "one.md"), "one");

    const first = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir, formats: FORMATS });
    const second = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir, formats: FORMATS });
    const firstManifest = await readManifest(dataDir, first.collectionId);
    const secondManifest = await readManifest(dataDir, second.collectionId);
    const rootsPath = join(dataDir, "imports", "source-roots.json");
    const rootsContents = await readFile(rootsPath, "utf8");

    expect(firstManifest.rootId).toMatch(/^[0-9a-f-]{36}$/);
    expect(secondManifest.rootId).toBe(firstManifest.rootId);
    expect(rootsContents).toContain(fixtureRoot);
    if (process.platform !== "win32") {
      expect((await lstat(rootsPath)).mode & 0o777).toBe(0o600);
    }
  });

  it("rejects an oversized source before publishing a partial collection", async () => {
    const fixtureRoot = join(workspace, "oversized");
    await mkdir(fixtureRoot);
    await writeFile(join(fixtureRoot, "large.pdf"), "too large");

    await expect(
      stageDirectoryForTest({
        selectedPath: fixtureRoot,
        dataDir,
        formats: FORMATS,
        maxTotalBytes: 4,
      }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    expect(entries.filter((entry) => basename(entry).endsWith(".partial"))).toEqual([]);
  });

  it.each([
    ["markdown", "guide.md"],
    ["html", "index.html"],
    ["pdf", "guide.pdf"],
    ["docx", "report.docx"],
  ] as const)("rejects an oversized %s file before copying", async (_kind, filename) => {
    const fixtureRoot = join(workspace, `oversized-${_kind}`);
    await mkdir(fixtureRoot);
    await writeFile(join(fixtureRoot, filename), "12345");

    // Shrink only the ceiling of the format under test, leaving the others
    // untouched: this asserts the per-format limit is what is enforced, not a
    // single global one that happens to be small.
    const formats = FORMATS.map((format) =>
      format.extensions.includes(extname(filename)) ? { ...format, maxBytes: 4 } : format,
    );

    await expect(
      stageDirectoryForTest({
        selectedPath: fixtureRoot,
        dataDir,
        formats,
      }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    expect(entries.filter((entry) => basename(entry).endsWith(".partial"))).toEqual([]);
  });

  it("rejects a source path replaced after copying from the original descriptor", async () => {
    const fixtureRoot = join(workspace, "replaced");
    await mkdir(fixtureRoot);
    const sourcePath = join(fixtureRoot, "guide.md");
    const backupPath = join(fixtureRoot, "guide.original.md");
    await writeFile(sourcePath, "original");

    await expect(
      stageDirectoryForTest({
        selectedPath: fixtureRoot,
        dataDir,
        formats: FORMATS,
        beforeSourceRecheck: async (path) => {
          await rename(path, backupPath);
          await writeFile(path, "replacement");
        },
      }),
    ).rejects.toMatchObject({ code: "BATCH_SOURCE_CHANGED" });

    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    expect(entries.filter((entry) => basename(entry).endsWith(".partial"))).toEqual([]);
  });
});
