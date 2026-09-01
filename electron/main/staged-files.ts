import { constants } from "node:fs";
import { lstat, mkdir, open, rename, unlink } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { basename, extname, join } from "node:path";

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
type DialogResult = { canceled: boolean; filePaths: string[] };
type ShowOpenDialog = (options: {
  properties: "openFile"[];
  filters: { name: string; extensions: string[] }[];
}) => Promise<DialogResult>;
export class StagedFileService {
  private readonly showOpenDialog: ShowOpenDialog;
  constructor(
    private opts: {
      dataDir: string;
      maxPdfBytes?: number;
      maxMarkdownBytes?: number;
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
