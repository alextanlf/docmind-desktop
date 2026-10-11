/**
 * 推导信号的中文名。
 *
 * 与后端 `Signal`（backend/app/corpus/candidates.py）逐字对齐：有测试直接读后端
 * 源文件，断言每个取值都有文案 —— 后端加一个信号而前端没跟上时，界面上会出现一个
 * 英文枚举名，而那正是这套"信号 → 证据"设计最不该出的错。
 */
export const SIGNAL_LABELS: Record<string, string> = {
  explicit: "你指定",
  seed: "种子表",
  documents: "文档引用",
  nomination: "模型提名",
  crawl_history: "抓取历史",
  upstream: "上游结果",
};

/** 不认识的取值原样显示：假装认识比露出枚举名更糟。 */
export function signalLabel(signal: string): string {
  return SIGNAL_LABELS[signal] ?? signal;
}
