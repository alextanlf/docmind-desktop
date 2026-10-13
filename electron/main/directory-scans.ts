import type { ImportFormat } from "./staged-files";
import { scanCancelled, scanSelectedDirectory, type StagedCollection } from "./staged-files";

/**
 * 目录扫描的**生命周期**：后台跑、报进度、可取消。
 *
 * 为什么需要它：扫描要把用户目录里每个文件完整读一遍算摘要（不复制），一个几百个文件的
 * 资料夹就是几分钟。从前这件事发生在"选完文件夹"那一次 IPC 调用的里面 —— 界面只能等，
 * 既看不到读到哪里，也没法喊停。这里把它变成：
 *
 * ```
 * 渲染层订阅(scanId) ──▶ start() 立刻返回 ──▶ 后台逐个文件算摘要
 *                        ├─ progress {scanned, total}
 *                        ├─ done     {collection}
 *                        ├─ cancelled
 *                        └─ error    {code, message}
 * ```
 *
 * 扫描**不进数据库、不可跨重启恢复**：它只产出一份清单，产物要么完整存在（`collections/<id>`），
 * 要么被清掉（`.partial`）。没有"半成品需要恢复"这种状态，所以也不需要状态机 ——
 * 与导入任务那种"会写远端、必须能补偿"的东西不是一类。
 */
export type ScanEvent =
  | { type: "progress"; scanned: number; total: number }
  | { type: "done"; collection: StagedCollection }
  | { type: "cancelled" }
  | { type: "error"; error: unknown };

type ScanOptions = {
  dataDir: string;
  formats: ImportFormat[];
  sessionToken?: string;
  maxFileCount?: number;
  maxTotalBytes?: number;
};

type RunningScan = { cancelled: boolean };

export class DirectoryScanService {
  private readonly running = new Map<string, RunningScan>();

  /**
   * 让用户挑一个目录并**在后台**扫描它。返回 scanId 时扫描已经开始，还没有结果。
   *
   * `scanId` **由调用方给**：它同时是事件流的路由键（`streamEventChannel(scanId)`）与取消
   * 用的标识。一个 id 走完全程是有意的 —— 内部再生成一个，取消就得在两张表之间翻译，
   * 而"取消找不到正在跑的扫描"这种错会静默发生（写 IPC 用例时正是这样踩到的）。
   *
   * `pickDirectory` 由调用方注入（真实实现是原生对话框，测试里是一个函数），
   * 这样"挑目录"与"扫描"的时序在测试里可以精确控制 —— 包括"挑完立刻取消"这种边界。
   */
  async chooseAndScan(
    options: ScanOptions,
    scanId: string,
    pickDirectory: () => Promise<string | null>,
    emit: (event: ScanEvent) => void,
  ): Promise<string | null> {
    const selected = await pickDirectory();
    // 对话框被取消：没有扫描在跑，也不需要清理。
    if (selected === null) return null;
    const state: RunningScan = { cancelled: false };
    this.running.set(scanId, state);
    // 故意不 await：调用方要的是"立刻拿到 scanId 去看进度"。
    void this.run(scanId, selected, options, emit);
    return scanId;
  }

  cancel(scanId: string): boolean {
    const state = this.running.get(scanId);
    if (!state) return false;
    // 只是置个标志：扫描体在每个文件的边界上自己看它。不在这里删表 ——
    // 让 run() 的 finally 统一收尾，否则"取消之后还在跑的那一步"会看到自己已经不在册。
    state.cancelled = true;
    return true;
  }

  isRunning(scanId: string): boolean {
    return this.running.has(scanId);
  }

  private async run(
    scanId: string,
    selected: string,
    options: ScanOptions,
    emit: (event: ScanEvent) => void,
  ): Promise<void> {
    const state = this.running.get(scanId);
    try {
      const collection = await scanSelectedDirectory(selected, {
        dataDir: options.dataDir,
        formats: options.formats,
        sessionToken: options.sessionToken,
        maxFileCount: options.maxFileCount,
        maxTotalBytes: options.maxTotalBytes,
        onProgress: (scanned, total) => emit({ type: "progress", scanned, total }),
        shouldCancel: () => state?.cancelled === true,
      });
      emit({ type: "done", collection });
    } catch (error) {
      // 取消不是失败：界面要的是"停了"，不是一条错误横幅。既认"标志位已置"，
      // 也认我们自己抛的 SCAN_CANCELLED（取消与失败可能同时发生）。
      if (state?.cancelled === true || isScanCancelled(error)) {
        emit({ type: "cancelled" });
      } else {
        // 错误原样交出去，由 IPC 层用既有的 serializeIpcError 变成"原因码 + 可重试"，
        // 文案住在前端 —— 与全仓库同一条约定。
        emit({ type: "error", error });
      }
    } finally {
      this.running.delete(scanId);
    }
  }
}

function isScanCancelled(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: unknown }).code === scanCancelled().code
  );
}
