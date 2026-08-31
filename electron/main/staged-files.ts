import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, rename, unlink } from "node:fs/promises";
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
  private async defaultDialog(
    options: Parameters<ShowOpenDialog>[0],
  ): Promise<DialogResult> {
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
  async stageSelected(
    source: string,
    kind: "pdf" | "markdown",
  ): Promise<StagedSource> {
    const stat = await lstat(source).catch(() => {
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Source unavailable");
    });
    if (!stat.isFile())
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Regular file required");
    const extension = extname(source).toLowerCase();
    const extensions = kind === "pdf" ? [".pdf"] : [".md", ".markdown"];
    if (!extensions.includes(extension))
      throw new StagedFileError("SOURCE_UNSUPPORTED", "Unsupported extension");
    const limit =
      kind === "pdf"
        ? (this.opts.maxPdfBytes ?? 100 * 1024 * 1024)
        : (this.opts.maxMarkdownBytes ?? 20 * 1024 * 1024);
    if (stat.size > limit)
      throw new StagedFileError(
        "SOURCE_TOO_LARGE",
        "Source exceeds size limit",
      );
    const id = randomUUID();
    const directory = join(this.opts.dataDir, "imports", "staging");
    const partial = join(directory, `${id}${extension}.partial`);
    const final = join(directory, `${id}${extension}`);
    try {
      await mkdir(directory, { recursive: true });
      const sourceHandle = await open(
        source,
        constants.O_RDONLY | (constants as any).O_NOFOLLOW,
      );
      const openedStat = await sourceHandle.stat();
      if (!openedStat.isFile() || openedStat.size !== stat.size)
        throw new Error("source changed");
      const contents = await readFile(sourceHandle);
      await sourceHandle.close();
      const destination = await open(
        partial,
        constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
        0o600,
      );
      await destination.write(contents);
      await destination.sync();
      await destination.close();
      await rename(partial, final);
    } catch {
      await unlink(partial).catch(() => {});
      throw new StagedFileError(
        "SOURCE_STAGE_FAILED",
        "Unable to stage source",
      );
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
