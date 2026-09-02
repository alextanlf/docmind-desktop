import { constants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  rm,
  unlink,
} from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { basename, extname, isAbsolute, join, relative, sep } from "node:path";

const MAX_DIRECTORY_FILES = 1_000;
const MAX_DIRECTORY_BYTES = 2 * 1024 ** 3;
const MAX_MARKDOWN_HTML_BYTES = 20 * 1024 * 1024;
const MAX_PDF_BYTES = 100 * 1024 * 1024;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

type DirectoryDialogOptions = { properties: ["openDirectory"] };
type FileDialogOptions = {
  properties: ["openFile"];
  filters: { name: string; extensions: string[] }[];
};
type DialogResult = { canceled: boolean; filePaths: string[] };
type ShowOpenDialog = (options: DirectoryDialogOptions | FileDialogOptions) => Promise<DialogResult>;

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
  maxFileCount?: number;
  maxTotalBytes?: number;
  maxPdfBytes?: number;
  maxMarkdownBytes?: number;
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
export type StagedSource = {
  stagedSourceId: string;
  kind: "staged_file";
  name: string;
  mediaType: string;
  sizeBytes: number;
};
export class StagedFileService {
  private readonly showOpenDialog: ShowOpenDialog;
  constructor(
    private opts: {
      dataDir: string;
      maxPdfBytes?: number;
      maxMarkdownBytes?: number;
      maxDirectoryFiles?: number;
      maxDirectoryBytes?: number;
      showOpenDialog?: ShowOpenDialog;
      copyFile?: never;
    },
  ) {
    this.showOpenDialog = opts.showOpenDialog ?? this.defaultDialog;
  }
  private async defaultDialog(options: Parameters<ShowOpenDialog>[0]): Promise<DialogResult> {
    const { dialog } = await import("electron");
    return dialog.showOpenDialog(options);
  }
  async chooseAndStage(kind: "pdf" | "markdown"): Promise<StagedSource | null> {
    const result = await this.showOpenDialog({
      properties: ["openFile"],
      filters:
        kind === "pdf"
          ? [{ name: "PDF", extensions: ["pdf"] }]
          : [{ name: "Markdown", extensions: ["md", "markdown"] }],
    });
    if (result.canceled || result.filePaths.length !== 1) return null;
    return this.stageSelected(result.filePaths[0], kind);
  }
  async stageDirectory(): Promise<StagedCollection | null> {
    const result = await this.showOpenDialog({ properties: ["openDirectory"] });
    if (result.canceled || result.filePaths.length !== 1) return null;
    return stageSelectedDirectory(result.filePaths[0], {
      dataDir: this.opts.dataDir,
      maxFileCount: this.opts.maxDirectoryFiles,
      maxTotalBytes: this.opts.maxDirectoryBytes,
      maxPdfBytes: this.opts.maxPdfBytes,
      maxMarkdownBytes: this.opts.maxMarkdownBytes,
    });
  }
  async stageSelected(source: string, kind: "pdf" | "markdown"): Promise<StagedSource> {
    const noFollow = (constants as NodeJS.Dict<number>).O_NOFOLLOW;
    if (typeof noFollow !== "number") {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Secure file access unavailable");
    }
    const stat = await lstat(source).catch(() => {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Source unavailable");
    });
    if (!stat.isFile()) throw new StagedFileError("SOURCE_UNSUPPORTED", "Regular file required");
    const extension = extname(source).toLowerCase();
    const extensions = kind === "pdf" ? [".pdf"] : [".md", ".markdown"];
    if (!extensions.includes(extension))
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Unsupported extension");
    const limit =
      kind === "pdf"
        ? (this.opts.maxPdfBytes ?? 100 * 1024 * 1024)
        : (this.opts.maxMarkdownBytes ?? 20 * 1024 * 1024);
    if (stat.size > limit)
      throw new StagedFileError("SOURCE_TOO_LARGE", "Source exceeds size limit");
    const id = randomUUID();
    const directory = join(this.opts.dataDir, "imports", "staging");
    const partial = join(directory, `${id}${extension}.partial`);
    const final = join(directory, `${id}${extension}`);
    try {
      await mkdir(directory, { recursive: true });
      const sourceHandle = await open(source, constants.O_RDONLY | noFollow);
      try {
        const openedStat = await sourceHandle.stat();
        if (!openedStat.isFile() || openedStat.size !== stat.size || openedStat.size > limit)
          throw new Error("source changed");
        const destination = await open(
          partial,
          constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
          0o600,
        );
        try {
          const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, limit));
          let position = 0;
          while (position < openedStat.size) {
            const { bytesRead } = await sourceHandle.read(
              buffer,
              0,
              Math.min(buffer.length, openedStat.size - position),
              position,
            );
            if (bytesRead === 0) throw new Error("source changed");
            let written = 0;
            while (written < bytesRead) {
              const { bytesWritten } = await destination.write(
                buffer,
                written,
                bytesRead - written,
              );
              if (
                !Number.isInteger(bytesWritten) ||
                bytesWritten <= 0 ||
                bytesWritten > bytesRead - written
              )
                throw new Error("destination write failed");
              written += bytesWritten;
            }
            position += bytesRead;
            if (position > limit) throw new Error("source changed");
          }
          const finalStat = await sourceHandle.stat();
          if (finalStat.size !== openedStat.size) throw new Error("source changed");
          const destinationStat = await destination.stat();
          if (destinationStat.size !== openedStat.size) throw new Error("destination write failed");
          await destination.sync();
        } finally {
          await destination.close().catch(() => {});
        }
      } finally {
        await sourceHandle.close().catch(() => {});
      }
      await rename(partial, final);
    } catch {
      await unlink(partial).catch(() => {});
      throw new StagedFileError("SOURCE_STAGE_FAILED", "Unable to stage source");
    }
    return {
      stagedSourceId: id,
      kind: "staged_file",
      name: basename(source),
      mediaType: kind === "pdf" ? "application/pdf" : "text/markdown",
      sizeBytes: stat.size,
    };
  }
}

export async function stageDirectoryForTest(options: {
  selectedPath: string;
  dataDir: string;
  maxFileCount?: number;
  maxTotalBytes?: number;
  maxPdfBytes?: number;
  maxMarkdownBytes?: number;
}): Promise<StagedCollection> {
  return stageSelectedDirectory(options.selectedPath, options);
}

async function stageSelectedDirectory(
  selectedPath: string,
  options: DirectoryStagingOptions,
): Promise<StagedCollection> {
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
  const maxPdfBytes = options.maxPdfBytes ?? MAX_PDF_BYTES;
  const maxMarkdownBytes = options.maxMarkdownBytes ?? MAX_MARKDOWN_HTML_BYTES;
  if (
    !Number.isInteger(maxFileCount) ||
    maxFileCount < 0 ||
    maxFileCount > MAX_DIRECTORY_FILES ||
    !Number.isSafeInteger(maxTotalBytes) ||
    maxTotalBytes < 0 ||
    maxTotalBytes > MAX_DIRECTORY_BYTES ||
    !Number.isSafeInteger(maxPdfBytes) ||
    maxPdfBytes < 0 ||
    maxPdfBytes > MAX_PDF_BYTES ||
    !Number.isSafeInteger(maxMarkdownBytes) ||
    maxMarkdownBytes < 0 ||
    maxMarkdownBytes > MAX_MARKDOWN_HTML_BYTES
  )
    throw batchLimitExceeded();
  const candidates = await collectCandidates(
    canonicalRoot,
    maxFileCount,
    maxTotalBytes,
    maxPdfBytes,
    maxMarkdownBytes,
  );
  const totalBytes = candidates.reduce((total, candidate) => total + candidate.sizeBytes, 0);
  const rootId = await getOrCreateRootId(options.dataDir, canonicalRoot).catch((error) => {
    if (error instanceof StagedFileError) throw error;
    throw batchSourceChanged();
  });
  const collectionId = randomUUID();
  const collectionsDir = join(options.dataDir, "imports", "staging", "collections");
  const partialCollection = join(collectionsDir, `${collectionId}.partial`);
  const finalCollection = join(collectionsDir, collectionId);

  try {
    await mkdir(join(partialCollection, "items"), { recursive: true, mode: 0o700 });
    const files: ManifestEntry[] = [];
    for (const candidate of candidates) {
      const stagedId = randomUUID();
      const stagedPath = join(partialCollection, "items", stagedId);
      const sha256 = await atomicCopyCandidate(candidate, canonicalRoot, stagedPath);
      files.push({
        relativePath: candidate.relativePath,
        stagedId,
        mediaType: candidate.mediaType,
        sizeBytes: candidate.sizeBytes,
        sha256,
      });
    }
    validateManifestEntries(files, maxFileCount, maxTotalBytes);
    await atomicWriteJson(join(partialCollection, "manifest.json"), { rootId, files });
    await rename(partialCollection, finalCollection);
  } catch (error) {
    await rm(partialCollection, { recursive: true, force: true }).catch(() => {});
    if (error instanceof StagedFileError) throw error;
    throw batchSourceChanged();
  }

  return {
    collectionId,
    displayName: (basename(canonicalRoot).trim() || "Folder").slice(0, 255),
    itemCount: candidates.length,
    totalBytes,
  };
}

async function collectCandidates(
  canonicalRoot: string,
  maxFileCount: number,
  maxTotalBytes: number,
  maxPdfBytes: number,
  maxMarkdownBytes: number,
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
      const mediaType = classifyMediaType(sourcePath);
      if (mediaType === null) continue;
      const relativePath = safeRelativePath(canonicalRoot, sourcePath);
      const sizeBytes = Number(stat.size);
      if (!Number.isSafeInteger(sizeBytes) || sizeBytes < 0) throw batchLimitExceeded();
      const maxFileBytes = mediaType === "application/pdf" ? maxPdfBytes : maxMarkdownBytes;
      if (sizeBytes > maxFileBytes) throw batchLimitExceeded();
      totalBytes += sizeBytes;
      if (candidates.length + 1 > maxFileCount || totalBytes > maxTotalBytes)
        throw batchLimitExceeded();
      candidates.push({
        sourcePath,
        relativePath,
        mediaType,
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

function classifyMediaType(sourcePath: string): string | null {
  switch (extname(sourcePath).toLowerCase()) {
    case ".pdf":
      return "application/pdf";
    case ".md":
    case ".markdown":
      return "text/markdown";
    case ".html":
    case ".htm":
      return "text/html";
    default:
      return null;
  }
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

async function atomicCopyCandidate(
  candidate: DirectoryCandidate,
  canonicalRoot: string,
  destinationPath: string,
): Promise<string> {
  const noFollow = (constants as NodeJS.Dict<number>).O_NOFOLLOW;
  if (typeof noFollow !== "number") throw batchSourceChanged();
  await assertInsideRoot(canonicalRoot, candidate.sourcePath);
  const observed = await lstat(candidate.sourcePath, { bigint: true }).catch(() => {
    throw batchSourceChanged();
  });
  if (!matchesCandidate(candidate, observed)) throw batchSourceChanged();

  const partialPath = `${destinationPath}.partial`;
  try {
    const source = await open(candidate.sourcePath, constants.O_RDONLY | noFollow).catch(() => {
      throw batchSourceChanged();
    });
    try {
      const opened = await source.stat({ bigint: true });
      if (!matchesCandidate(candidate, opened)) throw batchSourceChanged();
      const destination = await open(
        partialPath,
        constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
        0o600,
      );
      try {
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
          let written = 0;
          while (written < bytesRead) {
            const result = await destination.write(buffer, written, bytesRead - written);
            if (result.bytesWritten <= 0 || result.bytesWritten > bytesRead - written)
              throw batchSourceChanged();
            written += result.bytesWritten;
          }
          position += bytesRead;
        }
        const finalSourceStat = await source.stat({ bigint: true });
        const destinationStat = await destination.stat();
        if (
          !matchesCandidate(candidate, finalSourceStat) ||
          destinationStat.size !== candidate.sizeBytes
        )
          throw batchSourceChanged();
        await destination.sync();
        const digest = hash.digest("hex");
        if (!SHA256_PATTERN.test(digest)) throw batchSourceChanged();
        await destination.close();
        await rename(partialPath, destinationPath);
        return digest;
      } catch (error) {
        await destination.close().catch(() => {});
        throw error;
      }
    } finally {
      await source.close().catch(() => {});
    }
  } catch (error) {
    await unlink(partialPath).catch(() => {});
    if (error instanceof StagedFileError) throw error;
    throw batchSourceChanged();
  }
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
): void {
  if (files.length > maxFileCount) throw batchLimitExceeded();
  let totalBytes = 0;
  const stagedIds = new Set<string>();
  for (const entry of files) {
    const segments = entry.relativePath.split("/");
    if (
      isAbsolute(entry.relativePath) ||
      segments.some((segment) => segment === "" || segment === "." || segment === "..") ||
      classifyMediaType(entry.relativePath) !== entry.mediaType ||
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
