import { useEffect } from "react";
import type { BatchImport, ImportJob } from "../../../../shared/contracts";

/**
 * 导入成功之后进度栏自己让位，失败/取消则留在原地等用户处理。
 *
 * 三条规则各有理由：
 * - 成功是终局，再挂着一个 100% 的进度条只是占地方 —— 用户要看的文档已经在列表里；
 * - 失败/取消还没结束：错误原因与「重试导入」按钮是下一步操作的入口，收掉等于把入口藏起来；
 * - 成功也要停一下再收：这几秒是用户点「查看文档」的唯一窗口，收得太快等于没有。
 *
 * 单篇短，2500ms 够读完一行中文；批量导入条数多，用户常常在别的窗口等它跑完再回来，
 * 所以留得久一些。
 */
export const importSuccessDismissDelayMs = 2500;
export const batchSettledDismissDelayMs = 6000;

export type DismissDelays = {
  importAfterSuccessMs?: number;
  batchAfterSettledMs?: number;
};

/** `null` 表示不自动收（还没结束，或者失败/取消这类需要用户处理的状态）。 */
export function importAutoDismissDelay(
  state: ImportJob["state"] | null | undefined,
  delays: DismissDelays = {},
) {
  const delay = delays.importAfterSuccessMs ?? importSuccessDismissDelayMs;
  return state === "completed" ? delay : null;
}

export function batchAutoDismissDelay(
  state: BatchImport["state"] | null | undefined,
  delays: DismissDelays = {},
) {
  const delay = delays.batchAfterSettledMs ?? batchSettledDismissDelayMs;
  // 部分失败也让位，留的时间同样够用户看清「哪几个失败」：真要重试可以从文档列表再来一次。
  return state === "completed" || state === "completed_with_errors" ? delay : null;
}

/**
 * 按当前状态起一个一次性的收起定时器。
 *
 * 状态一变就重新计时（依赖里是算好的延迟），所以「失败 → 重试 → 成功」会走完整的一轮；
 * 还没结束的任务拿到 `null`，不会误设一个定时器。
 */
export function useImportAutoDismiss(delayMs: number | null, dismiss: () => void) {
  useEffect(() => {
    if (delayMs === null) return;
    const timer = window.setTimeout(dismiss, delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, dismiss]);
}
