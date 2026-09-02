import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { StagedFileService, stageDirectoryForTest } from "../../main/staged-files";

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

    const result = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir });

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

    await expect(service.stageDirectory()).resolves.toBeNull();
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
      stageDirectoryForTest({ selectedPath: tooManyFiles, dataDir }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const symlinkFixture = join(workspace, "links");
    await mkdir(symlinkFixture);
    await writeFile(join(symlinkFixture, "regular.md"), "safe");
    await writeFile(join(workspace, "outside.md"), "outside");
    await symlink(join(workspace, "outside.md"), join(symlinkFixture, "linked.md"));
    await symlink(workspace, join(symlinkFixture, "linked-directory"));
    await expect(
      stageDirectoryForTest({ selectedPath: symlinkFixture, dataDir }),
    ).resolves.toMatchObject({ itemCount: 1 });
  });

  it("reuses an opaque root id and protects its path registry with mode 0600", async () => {
    const fixtureRoot = join(workspace, "stable-root");
    await mkdir(fixtureRoot);
    await writeFile(join(fixtureRoot, "one.md"), "one");

    const first = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir });
    const second = await stageDirectoryForTest({ selectedPath: fixtureRoot, dataDir });
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
        maxTotalBytes: 4,
      }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    expect(entries.filter((entry) => basename(entry).endsWith(".partial"))).toEqual([]);
  });
});
