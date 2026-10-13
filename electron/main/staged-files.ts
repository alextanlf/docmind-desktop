import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, realpath, rename, rm, stat, unlink } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { basename, extname, isAbsolute, join, relative, sep } from "node:path";

import { DIRECTORY_DOMAIN, FILE_DOMAIN, pathToken } from "./path-tokens";
import { DirectoryScanService, type ScanEvent } from "./directory-scans";

// 扫描事件的类型从扫描生命周期那边来；这里转出去，调用方（含测试）只认一个入口。
export type { ScanEvent };

const MAX_DIRECTORY_FILES = 1_000;
const MAX_DIRECTORY_BYTES = 2 * 1024 ** 3;
/**
 * Absolute ceiling for one file, whatever a format declares.
 *
 * The per-format limit comes from the backend (`SourceFormat.maxBytes`); this
 * is the outer bound that keeps a malformed or hostile format declaration from
 * asking the main process to buffer something absurd. It is defence in depth,
 * not the policy — the policy is the backend's.
 */
const MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

/**
 * An importable format, as declared by the backend.
 *
 * The main process holds no list of these; it is handed one when the user
 * picks a file. `extensions` carry a leading dot (`.docx`), matching how the
 * backend declares them; the dialog wants them bare, so the conversion happens
 * at the one place that talks to the dialog.
 */
export type ImportFormat = {
  name: string;
  label: string;
  extensions: string[];
  mediaType: string;
  maxBytes: number;
};

type DirectoryDialogOptions = { properties: ["openDirectory"] };
type FileDialogOptions = {
  properties: ["openFile"];
  filters: { name: string; extensions: string[] }[];
};
type DialogResult = { canceled: boolean; filePaths: string[] };
type ShowOpenDialog = (
  options: DirectoryDialogOptions | FileDialogOptions,
) => Promise<DialogResult>;

export type StagedCollection = {
  collectionId: string;
  displayName: string;
  itemCount: number;
  totalBytes: number;
};

type DirectoryCandidate = {
  sourcePath: string;
  relativePath: string;
  mediaType: string;
  sizeBytes: number;
  device: bigint;
  inode: bigint;
  modifiedNs: bigint;
  changedNs: bigint;
};

type ManifestEntry = {
  relativePath: string;
  stagedId: string;
  mediaType: string;
  sizeBytes: number;
  sha256: string;
};

type DirectoryStagingOptions = {
  dataDir: string;
  formats: ImportFormat[];
  maxFileCount?: number;
  maxTotalBytes?: number;
  beforeSourceRecheck?: (sourcePath: string) => Promise<void> | void;
  /** 与后端共享的会话密钥：写链接型清单时要给用户授权的目录签名。 */
  sessionToken?: string;
  /**
   * 进度回调：`total` 来自遍历（读目录项，不读文件内容），所以它在第一个文件被读之前
   * 就是准的 —— 界面因此能显示"已扫描 n/N"而不是一个转不完的圈。
   */
  onProgress?: (scanned: number, total: number) => void;
  /**
   * 每读完一个文件问一次"还要继续吗"。返回 true 就中止并**清掉半成品清单目录**。
   *
   * 用回调而不是 AbortSignal：这里的取消点是明确的循环边界，而 AbortSignal 在这个
   * 代码库里没有先例（导入用的是状态机 + cancel_requested），保持一致更好懂。
   */
  shouldCancel?: () => boolean;
};

const registryLocks = new Map<string, Promise<void>>();

type CandidateStat = {
  isFile(): boolean;
  size: bigint;
  dev: bigint;
  ino: bigint;
  mtimeNs: bigint;
  ctimeNs: bigint;
};

export class StagedFileError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "StagedFileError";
  }
}
/**
 * 用户磁盘上的原件：**不复制**，只把路径交出去。
 *
 * 这里曾经还有一份"暂存副本"（`StagedSource`，把文件拷进应用目录再读副本）。单文件与
 * 目录批次都改成就地读之后它整个下线了：应用不再为用户文件保留任何副本。
 */
export type LinkedSource = {
  kind: "linked_file";
  path: string;
  name: string;
  mediaType: string;
  sizeBytes: number;
};

export class StagedFileService {
  private readonly showOpenDialog: ShowOpenDialog;
  constructor(
    private opts: {
      dataDir: string;
      maxDirectoryFiles?: number;
      maxDirectoryBytes?: number;
      showOpenDialog?: ShowOpenDialog;
      copyFile?: never;
      /** 与后端共享的会话密钥，用来给授权过的路径/目录签名。测试里可以缺省。 */
      sessionToken?: string;
    },
  ) {
    this.showOpenDialog = opts.showOpenDialog ?? this.defaultDialog;
  }
  /**
   * 对话框真的返回过的路径。
   *
   * 这是"路径 = 能力"里**能力的那一半**：光有签名不够 —— 如果谁送来一个路径就签一个，
   * 签名就成了一张空白支票，而渲染层（插件的 UI 就是渲染层代码）正是送路径的那个人。
   *
   * 只活在内存里，不需要跨重启：**每一个新路径都来自一次对话框**，而对话框只可能发生在
   * 当前进程活着的时候。
   */
  private readonly grantedPaths = new Set<string>();

  /** 目录扫描的生命周期（后台跑、报进度、可取消）：数据目录、限额、会话密钥与对话框
   *  它都要用，而这几样本来就在这个服务手上。 */
  private readonly scans = new DirectoryScanService();

  private async defaultDialog(options: Parameters<ShowOpenDialog>[0]): Promise<DialogResult> {
    const { dialog } = await import("electron");
    return dialog.showOpenDialog(options);
  }
  /**
   * 让用户挑一个文件，把它**当原件链接**进来 —— 不复制任何字节。
   *
   * 校验与 `stageSelected` 同一套（扩展名、普通文件、大小上限），区别只有一个：
   * 这里不写盘。省下的是用户磁盘上一份完整的副本，以及导入结束后的清理。
   */
  async chooseAndLink(format: ImportFormat): Promise<LinkedSource | null> {
    return this.pickLinkedFile(format.label, format.extensions, (path) =>
      format.extensions.includes(extname(path).toLowerCase()) ? format : null,
    );
  }

  /**
   * 让用户挑一个文件链接进来，但**不限定格式**。
   *
   * 重新定位原件时用：原件可能已经不在磁盘上了（这正是最常见的情形），所以文档记录里
   * 连媒体类型都没有，界面无从知道它原本是什么格式。把所有受支持格式的扩展名拼成一个
   * 过滤器，比让界面先猜一个格式名、再被"不支持的扩展名"拒掉要诚实。
   */
  async chooseAndLinkAny(formats: ImportFormat[]): Promise<LinkedSource | null> {
    return this.pickLinkedFile(
      "支持的文档",
      formats.flatMap((format) => format.extensions),
      (path) =>
        formats.find((format) => format.extensions.includes(extname(path).toLowerCase())) ?? null,
    );
  }

  private async pickLinkedFile(
    label: string,
    extensions: string[],
    resolveFormat: (path: string) => ImportFormat | null,
  ): Promise<LinkedSource | null> {
    const result = await this.showOpenDialog({
      properties: ["openFile"],
      filters: [
        {
          name: label,
          // 对话框要的是不带点的扩展名；后端声明的是带点的。
          extensions: extensions.map((extension) => extension.replace(/^\./, "")),
        },
      ],
    });
    if (result.canceled || result.filePaths.length !== 1) return null;
    const path = result.filePaths[0];
    const format = resolveFormat(path);
    if (format === null) throw new StagedFileError("SOURCE_UNSUPPORTED", "Unsupported extension");
    // 用 stat（跟随符号链接）而不是 lstat：用户在对话框里选了一个指向别处的链接，
    // 就该读它指向的东西 —— 他选的正是那串路径，签名保护的也是那串路径。
    const stats = await stat(path).catch(() => {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Source unavailable");
    });
    if (!stats.isFile()) throw new StagedFileError("SOURCE_UNSUPPORTED", "Regular file required");
    const limit = Math.min(format.maxBytes, MAX_SINGLE_FILE_BYTES);
    if (stats.size > limit)
      throw new StagedFileError("SOURCE_TOO_LARGE", "Source exceeds size limit");
    this.grantedPaths.add(path);
    return {
      kind: "linked_file",
      path,
      name: basename(path),
      mediaType: format.mediaType,
      sizeBytes: stats.size,
    };
  }

  /**
   * 给一个**对话框授权过的**路径签名；没授权过的直接拒绝。
   *
   * 拒绝而不是照签，是这套机制的关键：签名只证明"主进程认可这个路径"，
   * 而主进程认可的依据只能是"它来自一次用户操作"。
   */
  signGrantedPath(path: string): string {
    if (!this.grantedPaths.has(path)) {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Path was not granted by a file dialog");
    }
    if (!this.opts.sessionToken) {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Signing unavailable");
    }
    return pathToken(this.opts.sessionToken, FILE_DOMAIN, path);
  }

  /**
   * 让用户挑一个目录，**在后台就地扫描**它（不复制任何字节），边扫边报进度。
   *
   * 立刻返回 scanId 供订阅；进度与结果走 `emit`。扫描要把每个文件完整读一遍算摘要，
   * 几百个文件就是几分钟 —— 一问一答的形态会让界面只能干等，既看不到进度也没法取消。
   *
   * 只写一份清单（相对路径 + 大小 + sha256）；后端读取时重新核对摘要，所以
   * "用户确认的内容 == 真正导入的内容"没有降级 —— 见 `docs/本地原件直读方案.md` 第五节。
   */
  async scanDirectory(
    formats: ImportFormat[],
    scanId: string,
    emit: (event: ScanEvent) => void,
  ): Promise<string | null> {
    return this.scans.chooseAndScan(
      {
        dataDir: this.opts.dataDir,
        formats,
        sessionToken: this.opts.sessionToken,
        maxFileCount: this.opts.maxDirectoryFiles,
        maxTotalBytes: this.opts.maxDirectoryBytes,
      },
      scanId,
      async () => {
        const result = await this.showOpenDialog({ properties: ["openDirectory"] });
        if (result.canceled || result.filePaths.length !== 1) return null;
        return result.filePaths[0];
      },
      emit,
    );
  }

  /** 取消一个在跑的扫描。返回 false 表示它已经结束了（取消是幂等的）。 */
  cancelScan(scanId: string): boolean {
    return this.scans.cancel(scanId);
  }
}

export async function scanDirectoryForTest(options: {
  selectedPath: string;
  dataDir: string;
  formats: ImportFormat[];
  maxFileCount?: number;
  maxTotalBytes?: number;
  beforeSourceRecheck?: (sourcePath: string) => Promise<void> | void;
  sessionToken?: string;
}): Promise<StagedCollection> {
  return scanSelectedDirectory(options.selectedPath, options);
}

type PreparedDirectoryScan = {
  canonicalRoot: string;
  rootId: string;
  candidates: DirectoryCandidate[];
  totalBytes: number;
  collectionId: string;
  partialCollection: string;
  finalCollection: string;
  maxFileCount: number;
  maxTotalBytes: number;
};

/**
 * 两条路（复制与就地扫描）共用的准备阶段：canonical 化、限额、候选清单、rootId。
 *
 * 抽出来是因为两条路**必须**在这些检查上完全一致 —— 放宽其中任何一条，
 * "这个目录里到底什么会被导入"就会有两个答案。
 */
async function prepareDirectoryScan(
  selectedPath: string,
  options: DirectoryStagingOptions,
): Promise<PreparedDirectoryScan> {
  const selectedStat = await lstat(selectedPath, { bigint: true }).catch(() => {
    throw batchSourceChanged();
  });
  if (!selectedStat.isDirectory() || selectedStat.isSymbolicLink()) throw batchSourceChanged();

  const canonicalRoot = await realpath(selectedPath).catch(() => {
    throw batchSourceChanged();
  });
  const rootStat = await lstat(canonicalRoot, { bigint: true }).catch(() => {
    throw batchSourceChanged();
  });
  if (
    !rootStat.isDirectory() ||
    rootStat.isSymbolicLink() ||
    rootStat.dev !== selectedStat.dev ||
    rootStat.ino !== selectedStat.ino
  )
    throw batchSourceChanged();

  const maxFileCount = options.maxFileCount ?? MAX_DIRECTORY_FILES;
  const maxTotalBytes = options.maxTotalBytes ?? MAX_DIRECTORY_BYTES;
  if (
    !Number.isInteger(maxFileCount) ||
    maxFileCount < 0 ||
    maxFileCount > MAX_DIRECTORY_FILES ||
    !Number.isSafeInteger(maxTotalBytes) ||
    maxTotalBytes < 0 ||
    maxTotalBytes > MAX_DIRECTORY_BYTES
  )
    throw batchLimitExceeded();
  const candidates = await collectCandidates(
    canonicalRoot,
    options.formats,
    maxFileCount,
    maxTotalBytes,
  );
  const totalBytes = candidates.reduce((total, candidate) => total + candidate.sizeBytes, 0);
  const rootId = await getOrCreateRootId(options.dataDir, canonicalRoot).catch((error) => {
    if (error instanceof StagedFileError) throw error;
    throw batchSourceChanged();
  });
  const collectionId = randomUUID();
  const collectionsDir = join(options.dataDir, "imports", "staging", "collections");
  return {
    canonicalRoot,
    rootId,
    candidates,
    totalBytes,
    collectionId,
    partialCollection: join(collectionsDir, `${collectionId}.partial`),
    finalCollection: join(collectionsDir, collectionId),
    maxFileCount,
    maxTotalBytes,
  };
}

/**
 * 就地扫描一个目录：**算摘要，不复制任何字节**。
 *
 * 清单因此从"一堆副本的索引"变成"一个指针"：
 *
 * ```
 * { rootId, rootPath, rootToken, files: [{ relativePath, stagedId, mediaType, sizeBytes, sha256 }] }
 * ```
 *
 * `rootPath` 是给后端读的绝对路径，必须带主进程签名（目录授权，域与单文件分开）——
 * 渲染层既看不到它，也造不出可用的签名。清单由主进程直接写入暂存目录，
 * 所以这条授权**不经过渲染层**，与单文件那条（走 IPC 时补签名）不同。
 *
 * `stagedId` 仍然给每一项分配一个 UUID：它只是个句柄，用来把清单条目与批次项
 * （`cached_source_json`）对上；两种形态因此共用同一套批次数据结构。
 */
export async function scanSelectedDirectory(
  selectedPath: string,
  options: DirectoryStagingOptions,
): Promise<StagedCollection> {
  const prepared = await prepareDirectoryScan(selectedPath, options);
  if (!options.sessionToken) throw batchSourceChanged();
  const total = prepared.candidates.length;
  // 遍历（读目录项）在 prepare 里已经完成，所以这里报的总数是准的。
  // 先报一次 0/N：界面不必等到第一个文件读完才知道"要做多少"。
  options.onProgress?.(0, total);
  try {
    await mkdir(prepared.partialCollection, { recursive: true, mode: 0o700 });
    const files: ManifestEntry[] = [];
    for (const [index, candidate] of prepared.candidates.entries()) {
      if (options.shouldCancel?.()) throw scanCancelled();
      files.push({
        relativePath: candidate.relativePath,
        stagedId: randomUUID(),
        mediaType: candidate.mediaType,
        sizeBytes: candidate.sizeBytes,
        sha256: await hashCandidate(candidate, prepared.canonicalRoot, options.beforeSourceRecheck),
      });
      options.onProgress?.(index + 1, total);
    }
    // 写清单之前再看一眼：用户按取消之后不该还落下一份可用清单。
    if (options.shouldCancel?.()) throw scanCancelled();
    validateManifestEntries(files, prepared.maxFileCount, prepared.maxTotalBytes, options.formats);
    await atomicWriteJson(join(prepared.partialCollection, "manifest.json"), {
      rootId: prepared.rootId,
      files,
      rootPath: prepared.canonicalRoot,
      rootToken: pathToken(options.sessionToken, DIRECTORY_DOMAIN, prepared.canonicalRoot),
    });
    await rename(prepared.partialCollection, prepared.finalCollection);
  } catch (error) {
    // 半成品一律清掉 —— 取消与失败走同一条清理路径，不留 .partial 目录。
    await rm(prepared.partialCollection, { recursive: true, force: true }).catch(() => {});
    if (error instanceof StagedFileError) throw error;
    throw batchSourceChanged();
  }
  return {
    collectionId: prepared.collectionId,
    displayName: (basename(prepared.canonicalRoot).trim() || "Folder").slice(0, 255),
    itemCount: prepared.candidates.length,
    totalBytes: prepared.totalBytes,
  };
}

/** 用户在扫描途中按了取消。单独一个错误码，好让上层把"取消"和"失败"分开对待。 */
export function scanCancelled(): StagedFileError {
  return new StagedFileError("SCAN_CANCELLED", "Directory scan cancelled");
}

/**
 * 只读一个候选文件并算出 sha256 —— `atomicCopyCandidate` 去掉"写副本"那半边。
 *
 * 安全检查一条不少：不跟随符号链接、打开前后都用描述符核对身份（设备号/inode/mtime/ctime、
 * 大小），读完之后再从路径复查一次。少了这些，"扫描时算的摘要"就可能对应一个已经被换掉的
 * 文件，而用户确认的内容与实际导入的内容就对不上了。
 */
async function hashCandidate(
  candidate: DirectoryCandidate,
  canonicalRoot: string,
  beforeSourceRecheck?: (sourcePath: string) => Promise<void> | void,
): Promise<string> {
  const noFollow = (constants as NodeJS.Dict<number>).O_NOFOLLOW;
  if (typeof noFollow !== "number") throw batchSourceChanged();
  await assertInsideRoot(canonicalRoot, candidate.sourcePath);
  const observed = await lstat(candidate.sourcePath, { bigint: true }).catch(() => {
    throw batchSourceChanged();
  });
  if (!matchesCandidate(candidate, observed)) throw batchSourceChanged();

  const source = await open(candidate.sourcePath, constants.O_RDONLY | noFollow).catch(() => {
    throw batchSourceChanged();
  });
  try {
    const opened = await source.stat({ bigint: true });
    if (!matchesCandidate(candidate, opened)) throw batchSourceChanged();
    const hash = createHash("sha256");
    const buffer = Buffer.allocUnsafe(64 * 1024);
    let position = 0;
    while (position < candidate.sizeBytes) {
      const { bytesRead } = await source.read(
        buffer,
        0,
        Math.min(buffer.length, candidate.sizeBytes - position),
        position,
      );
      if (bytesRead <= 0) throw batchSourceChanged();
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
    const finalSourceStat = await source.stat({ bigint: true });
    if (!matchesCandidate(candidate, finalSourceStat)) throw batchSourceChanged();
    await beforeSourceRecheck?.(candidate.sourcePath);
    const postStat = await lstat(candidate.sourcePath, { bigint: true }).catch(() => {
      throw batchSourceChanged();
    });
    if (!matchesCandidate(candidate, postStat)) throw batchSourceChanged();
    const digest = hash.digest("hex");
    if (!SHA256_PATTERN.test(digest)) throw batchSourceChanged();
    return digest;
  } finally {
    await source.close().catch(() => {});
  }
}

async function collectCandidates(
  canonicalRoot: string,
  formats: ImportFormat[],
  maxFileCount: number,
  maxTotalBytes: number,
): Promise<DirectoryCandidate[]> {
  const candidates: DirectoryCandidate[] = [];
  let totalBytes = 0;

  const walk = async (directory: string): Promise<void> => {
    const entries = await readdir(directory, { withFileTypes: true }).catch(() => {
      throw batchSourceChanged();
    });
    entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
    for (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      const sourcePath = join(directory, entry.name);
      const stat = await lstat(sourcePath, { bigint: true }).catch(() => {
        throw batchSourceChanged();
      });
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) {
        await assertInsideRoot(canonicalRoot, sourcePath);
        await walk(sourcePath);
        continue;
      }
      if (!stat.isFile()) continue;
      const format = formatForPath(sourcePath, formats);
      if (format === null) continue;
      const relativePath = safeRelativePath(canonicalRoot, sourcePath);
      const sizeBytes = Number(stat.size);
      if (!Number.isSafeInteger(sizeBytes) || sizeBytes < 0) throw batchLimitExceeded();
      if (sizeBytes > Math.min(format.maxBytes, MAX_SINGLE_FILE_BYTES)) throw batchLimitExceeded();
      totalBytes += sizeBytes;
      if (candidates.length + 1 > maxFileCount || totalBytes > maxTotalBytes)
        throw batchLimitExceeded();
      candidates.push({
        sourcePath,
        relativePath,
        mediaType: format.mediaType,
        sizeBytes,
        device: stat.dev,
        inode: stat.ino,
        modifiedNs: stat.mtimeNs,
        changedNs: stat.ctimeNs,
      });
    }
  };

  await walk(canonicalRoot);
  candidates.sort((left, right) =>
    left.relativePath < right.relativePath ? -1 : left.relativePath > right.relativePath ? 1 : 0,
  );
  return candidates;
}

/**
 * The format owning one path's extension, or ``null`` if nothing claims it.
 *
 * Unknown files are skipped rather than rejected: an imported folder routinely
 * holds assets, and one unreadable file is not a reason to refuse the whole
 * directory.
 */
function formatForPath(sourcePath: string, formats: ImportFormat[]): ImportFormat | null {
  const extension = extname(sourcePath).toLowerCase();
  if (!extension) return null;
  return formats.find((format) => format.extensions.includes(extension)) ?? null;
}

function safeRelativePath(root: string, sourcePath: string): string {
  const nativePath = relative(root, sourcePath);
  const segments = nativePath.split(sep);
  if (
    nativePath.length === 0 ||
    isAbsolute(nativePath) ||
    segments.some(
      (segment) =>
        segment.length === 0 || segment === "." || segment === ".." || segment.includes("\\"),
    )
  )
    throw batchSourceChanged();
  return segments.join("/");
}

async function assertInsideRoot(root: string, sourcePath: string): Promise<void> {
  const nativePath = relative(root, sourcePath);
  if (nativePath === "" || isAbsolute(nativePath) || nativePath.split(sep).includes(".."))
    throw batchSourceChanged();
  const segments = nativePath.split(sep);
  let current = root;
  for (const segment of segments.slice(0, -1)) {
    current = join(current, segment);
    const stat = await lstat(current).catch(() => {
      throw batchSourceChanged();
    });
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw batchSourceChanged();
  }
  const canonicalPath = await realpath(sourcePath).catch(() => {
    throw batchSourceChanged();
  });
  const relativePath = relative(root, canonicalPath);
  if (relativePath === "" || isAbsolute(relativePath) || relativePath.split(sep).includes(".."))
    throw batchSourceChanged();
}

function matchesCandidate(candidate: DirectoryCandidate, stat: CandidateStat): boolean {
  return (
    stat.isFile() &&
    stat.size === BigInt(candidate.sizeBytes) &&
    stat.dev === candidate.device &&
    stat.ino === candidate.inode &&
    stat.mtimeNs === candidate.modifiedNs &&
    stat.ctimeNs === candidate.changedNs
  );
}

function validateManifestEntries(
  files: ManifestEntry[],
  maxFileCount: number,
  maxTotalBytes: number,
  formats: ImportFormat[],
): void {
  if (files.length > maxFileCount) throw batchLimitExceeded();
  let totalBytes = 0;
  const stagedIds = new Set<string>();
  for (const entry of files) {
    const segments = entry.relativePath.split("/");
    const format = formatForPath(entry.relativePath, formats);
    if (
      isAbsolute(entry.relativePath) ||
      segments.some((segment) => segment === "" || segment === "." || segment === "..") ||
      format === null ||
      format.mediaType !== entry.mediaType ||
      !UUID_PATTERN.test(entry.stagedId) ||
      stagedIds.has(entry.stagedId) ||
      !SHA256_PATTERN.test(entry.sha256)
    )
      throw batchSourceChanged();
    stagedIds.add(entry.stagedId);
    totalBytes += entry.sizeBytes;
    if (!Number.isSafeInteger(entry.sizeBytes) || entry.sizeBytes < 0 || totalBytes > maxTotalBytes)
      throw batchLimitExceeded();
  }
}

async function getOrCreateRootId(dataDir: string, canonicalRoot: string): Promise<string> {
  return withRegistryLock(dataDir, async () => {
    const importsDir = join(dataDir, "imports");
    const registryPath = join(importsDir, "source-roots.json");
    await mkdir(importsDir, { recursive: true, mode: 0o700 });
    const registry = await readRootRegistry(registryPath);
    const existing = registry[canonicalRoot];
    if (existing !== undefined) return existing;
    const rootId = randomUUID();
    await atomicWriteJson(registryPath, { ...registry, [canonicalRoot]: rootId });
    return rootId;
  });
}

async function readRootRegistry(registryPath: string): Promise<Record<string, string>> {
  const noFollow = (constants as NodeJS.Dict<number>).O_NOFOLLOW;
  if (typeof noFollow !== "number") throw batchSourceChanged();
  let handle: Awaited<ReturnType<typeof open>>;
  try {
    handle = await open(registryPath, constants.O_RDONLY | noFollow);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw batchSourceChanged();
  }
  let contents: string;
  try {
    const stat = await handle.stat();
    if (!stat.isFile()) throw batchSourceChanged();
    await handle.chmod(0o600).catch((error: NodeJS.ErrnoException) => {
      if (process.platform !== "win32") throw error;
    });
    contents = await handle.readFile("utf8");
  } catch {
    throw batchSourceChanged();
  } finally {
    await handle.close().catch(() => {});
  }
  try {
    const value: unknown = JSON.parse(contents);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid");
    const entries = Object.entries(value);
    if (entries.some(([path, rootId]) => !isAbsolute(path) || !UUID_PATTERN.test(String(rootId))))
      throw new Error("invalid");
    return Object.fromEntries(entries) as Record<string, string>;
  } catch {
    throw batchSourceChanged();
  }
}

async function atomicWriteJson(path: string, value: unknown): Promise<void> {
  const partialPath = `${path}.${randomUUID()}.partial`;
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(
      partialPath,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
      0o600,
    );
    const contents = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
    let offset = 0;
    while (offset < contents.length) {
      const { bytesWritten } = await handle.write(contents, offset, contents.length - offset);
      if (bytesWritten <= 0 || bytesWritten > contents.length - offset) throw batchSourceChanged();
      offset += bytesWritten;
    }
    await handle.sync();
    await handle.chmod(0o600).catch((error: NodeJS.ErrnoException) => {
      if (process.platform !== "win32") throw error;
    });
    await handle.close();
    handle = undefined;
    await rename(partialPath, path);
  } catch (error) {
    await handle?.close().catch(() => {});
    await unlink(partialPath).catch(() => {});
    throw error;
  }
}

async function withRegistryLock<T>(dataDir: string, operation: () => Promise<T>): Promise<T> {
  const previous = registryLocks.get(dataDir) ?? Promise.resolve();
  let release: () => void = () => {};
  const current = new Promise<void>((resolve) => {
    release = resolve;
  });
  const queued = previous.then(() => current);
  registryLocks.set(dataDir, queued);
  await previous;
  try {
    return await operation();
  } finally {
    release();
    if (registryLocks.get(dataDir) === queued) registryLocks.delete(dataDir);
  }
}

function batchLimitExceeded(): StagedFileError {
  return new StagedFileError("BATCH_LIMIT_EXCEEDED", "Directory exceeds batch limits");
}

function batchSourceChanged(): StagedFileError {
  return new StagedFileError("BATCH_SOURCE_CHANGED", "Directory source changed");
}
