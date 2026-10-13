import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, extname, join } from "node:path";
import { DIRECTORY_DOMAIN, pathToken } from "../../main/path-tokens";
import { StagedFileService, scanDirectoryForTest, type ScanEvent } from "../../main/staged-files";

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
  /** 链接型：用户授权的那个目录（规范化后的绝对路径）+ 主进程签名。 */
  rootPath?: string;
  rootToken?: string;
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

/**
 * 扫描标识由调用方给（它同时是事件流的路由键与取消用的键）。
 *
 * 用固定值而不是随机值：测试要能对"用同一个 id 能取消到"这件事下断言 ——
 * 内部再生成一个 id 这种错，正是随机 id 会让它静默通过的地方。
 */
const SCAN_ID = "00000000-0000-4000-8000-0000000000aa";

/** 与主进程共享的会话密钥：链接型清单里的目录签名要用它。 */
const TOKEN = "directory-scan-test-token";

/** 等一个后台任务推进到某个状态。扫描是 fire-and-forget，没有 promise 可 await。 */
async function waitFor(condition: () => boolean, timeoutMs = 5_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("等待超时");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

describe("linked directory scan", () => {
  let workspace: string;
  let dataDir: string;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "docmind-directory-"));
    dataDir = join(workspace, "data");
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  it("scans supported regular files without copying a single byte", async () => {
    const fixtureRoot = join(workspace, "fixture");
    await mkdir(join(fixtureRoot, "docs"), { recursive: true });
    await writeFile(join(fixtureRoot, "guide.pdf"), "%PDF-1.7\n");
    await writeFile(join(fixtureRoot, "docs", "readme.md"), "# Read me\n");
    await writeFile(join(fixtureRoot, "index.html"), "<h1>Index</h1>\n");
    await writeFile(join(fixtureRoot, "ignored.txt"), "not supported\n");

    const result = await scanDirectoryForTest({
      sessionToken: TOKEN,
      selectedPath: fixtureRoot,
      dataDir,
      formats: FORMATS,
    });

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
    // 链接型：清单里记着用户授权的那个目录（绝对路径 + 主进程签名），
    // 而**暂存目录里一个字节的副本都没有** —— 这正是这次改动的全部意义。
    // 记的是**规范化之后**的路径（macOS 上 /var 其实是 /private/var 的链接），
    // 后端解析目录之后要拿它做包含性比较，两边必须是同一个字符串。
    const canonicalRoot = await realpath(fixtureRoot);
    expect(manifest.rootPath).toBe(canonicalRoot);
    expect(pathToken(TOKEN, DIRECTORY_DOMAIN, canonicalRoot)).toBe(manifest.rootToken);
    await expect(
      readdir(join(dataDir, "imports", "staging", "collections", result.collectionId, "items")),
    ).rejects.toMatchObject({ code: "ENOENT" });
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

    // 取消对话框：拿不到扫描标识，也不会发出任何事件 —— 没有东西需要清理。
    const events: unknown[] = [];
    await expect(
      service.scanDirectory(FORMATS, SCAN_ID, (event) => events.push(event)),
    ).resolves.toBeNull();
    expect(events).toEqual([]);
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
      scanDirectoryForTest({
        selectedPath: tooManyFiles,
        dataDir,
        formats: FORMATS,
        sessionToken: TOKEN,
      }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const symlinkFixture = join(workspace, "links");
    await mkdir(symlinkFixture);
    await writeFile(join(symlinkFixture, "regular.md"), "safe");
    await writeFile(join(workspace, "outside.md"), "outside");
    await symlink(join(workspace, "outside.md"), join(symlinkFixture, "linked.md"));
    await symlink(workspace, join(symlinkFixture, "linked-directory"));
    await expect(
      scanDirectoryForTest({
        selectedPath: symlinkFixture,
        dataDir,
        formats: FORMATS,
        sessionToken: TOKEN,
      }),
    ).resolves.toMatchObject({ itemCount: 1 });
  });

  it("reuses an opaque root id and protects its path registry with mode 0600", async () => {
    const fixtureRoot = join(workspace, "stable-root");
    await mkdir(fixtureRoot);
    await writeFile(join(fixtureRoot, "one.md"), "one");

    const first = await scanDirectoryForTest({
      sessionToken: TOKEN,
      selectedPath: fixtureRoot,
      dataDir,
      formats: FORMATS,
    });
    const second = await scanDirectoryForTest({
      sessionToken: TOKEN,
      selectedPath: fixtureRoot,
      dataDir,
      formats: FORMATS,
    });
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
      scanDirectoryForTest({
        sessionToken: TOKEN,
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
  ] as const)("rejects an oversized %s file before hashing", async (_kind, filename) => {
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
      scanDirectoryForTest({
        sessionToken: TOKEN,
        selectedPath: fixtureRoot,
        dataDir,
        formats,
      }),
    ).rejects.toMatchObject({ code: "BATCH_LIMIT_EXCEEDED" });

    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    expect(entries.filter((entry) => basename(entry).endsWith(".partial"))).toEqual([]);
  });

  it("rejects a source path replaced after being hashed", async () => {
    const fixtureRoot = join(workspace, "replaced");
    await mkdir(fixtureRoot);
    const sourcePath = join(fixtureRoot, "guide.md");
    const backupPath = join(fixtureRoot, "guide.original.md");
    await writeFile(sourcePath, "original");

    await expect(
      scanDirectoryForTest({
        sessionToken: TOKEN,
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

/** 独立的一套临时目录：这组必须有自己的 beforeEach，不能借用上面的 `workspace`。 */
describe("清单的跨语言契约", () => {
  let workspace: string;
  let dataDir: string;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "docmind-manifest-contract-"));
    dataDir = join(workspace, "data");
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  /**
   * 清单的**键名**是跨语言契约：主进程写 JSON，后端用 pydantic 的 alias 读它。
   *
   * 两边各写各的测试都发现不了拼写漂移 —— Python 那份是自己手写的 JSON，
   * 主进程这份只断言自己写出来的形状。所以这里直接去读后端的模型声明，
   * 逐字比对（与 `contracts.test.ts` 里 MODEL_PRESET_IDS / SOURCE_KINDS 同一套做法）。
   */
  it("writes only keys the backend declares", async () => {
    const fixtureRoot = join(workspace, "contract");
    await mkdir(fixtureRoot, { recursive: true });
    await writeFile(join(fixtureRoot, "guide.md"), "# Guide\n");

    const result = await scanDirectoryForTest({
      selectedPath: fixtureRoot,
      dataDir,
      formats: FORMATS,
      sessionToken: TOKEN,
    });
    const manifest = JSON.parse(
      await readFile(
        join(dataDir, "imports", "staging", "collections", result.collectionId, "manifest.json"),
        "utf8",
      ),
    ) as Record<string, unknown>;

    // 从 cwd 拼（vitest 的 cwd 是仓库根目录）：这个环境里 `import.meta.url` 不是
    // file: URL，`fileURLToPath` 会直接拒绝。
    const backendSource = await readFile(
      join(process.cwd(), "backend/app/document/staging_manifest.py"),
      "utf8",
    );
    // 逐行扫：带 alias 的用 alias，没写 alias 的（`files`）用字段名当 JSON 键。
    // 不用正则字符类拼这一层，免得转义在字符串里被吃掉反而看不出问题。
    const declared = new Set<string>();
    for (const line of backendSource.split("\n")) {
      const alias = line.match(/alias="([a-zA-Z]+)"/);
      if (alias) {
        declared.add(alias[1]);
        continue;
      }
      // 字段名可能带数字（`sha256`）—— 用 `[a-z_]+` 会把它整条漏掉。
      const field = line.match(/^ {4}([A-Za-z_][A-Za-z0-9_]*):/);
      if (field) declared.add(field[1]);
    }

    const entry = (manifest.files as Record<string, unknown>[])[0];
    for (const key of [...Object.keys(manifest), ...Object.keys(entry)]) {
      expect(declared, `后端没有声明清单字段 ${key}`).toContain(key);
    }
  });
});

/**
 * 后台扫描：进度、取消、以及取消之后的现场。
 *
 * 这一组守的是"扫描不再是让界面干等的一问一答"：进度必须在**第一个文件读完之前**
 * 就有总数（否则界面只能转圈），取消之后不能留下半成品清单目录。
 */
describe("目录扫描的进度与取消", () => {
  let workspace: string;
  let dataDir: string;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "docmind-scan-progress-"));
    dataDir = join(workspace, "data");
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  async function fixture(name: string, count: number): Promise<string> {
    const root = join(workspace, name);
    await mkdir(root, { recursive: true });
    for (let index = 0; index < count; index++) {
      await writeFile(join(root, `doc-${String(index).padStart(3, "0")}.md`), `# ${index}\n`);
    }
    return root;
  }

  function serviceFor(root: string) {
    return new StagedFileService({
      dataDir,
      sessionToken: TOKEN,
      showOpenDialog: async () => ({ canceled: false, filePaths: [root] }),
    });
  }

  it("reports the total before the first file is read, then counts up", async () => {
    const root = await fixture("three", 3);
    const events: ScanEvent[] = [];

    const scanId = SCAN_ID;
    await serviceFor(root).scanDirectory(FORMATS, SCAN_ID, (event) => events.push(event));
    expect(scanId).not.toBeNull();
    await waitFor(() => events.some((event) => event.type === "done"));

    const progress = events.filter((event) => event.type === "progress");
    // 第一条进度就是 0/3：总数来自遍历目录项，不来自读文件。
    expect(progress[0]).toEqual({ type: "progress", scanned: 0, total: 3 });
    expect(progress.map((event) => event.scanned)).toEqual([0, 1, 2, 3]);
    expect(events.at(-1)).toMatchObject({ type: "done" });
  });

  it("cancels mid-scan and leaves no half-written collection behind", async () => {
    const root = await fixture("many", 40);
    const events: ScanEvent[] = [];
    const service = serviceFor(root);

    await service.scanDirectory(FORMATS, SCAN_ID, (event) => {
      events.push(event);
      // 一看到进度就取消：模拟用户按下取消按钮。
      if (event.type === "progress" && event.scanned >= 2) service.cancelScan(SCAN_ID);
    });
    await waitFor(() => events.some((event) => event.type === "cancelled"));

    // 取消是"停下来"，不是失败（失败会走 error 事件）。
    expect(events.some((event) => event.type === "error")).toBe(false);
    expect(events.some((event) => event.type === "done")).toBe(false);
    // **而且要真的早停**：40 个文件里，取消发生在第 2 个之后，就不该再往下读。
    // 只断言"最后是 cancelled"是不够的 —— 循环里那道检查被拿掉之后，
    // 扫描会老老实实把 40 个文件读完、再在写清单前被拦下，最终状态一模一样，
    // 但用户会觉得取消按钮坏了（还要再等几分钟）。这个数字就是那个区别。
    const progress = events.filter((event) => event.type === "progress");
    expect(progress.at(-1)).toMatchObject({ scanned: 2, total: 40 });
    const collections = join(dataDir, "imports", "staging", "collections");
    const entries = await readdir(collections).catch(() => []);
    // 既没有可用的集合，也没有 .partial 残骸。
    expect(entries).toEqual([]);
    expect(service.cancelScan(SCAN_ID)).toBe(false);
  });

  it("cannot be cancelled once it has finished", async () => {
    const root = await fixture("done", 2);
    const events: ScanEvent[] = [];
    const service = serviceFor(root);

    await service.scanDirectory(FORMATS, SCAN_ID, (event) => events.push(event));
    await waitFor(() => events.some((event) => event.type === "done"));

    // 跑完之后取消不生效：它已经不在册了（幂等，不抛错）。
    expect(service.cancelScan(SCAN_ID)).toBe(false);
  });
});
