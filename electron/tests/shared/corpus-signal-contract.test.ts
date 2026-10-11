import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SIGNAL_LABELS, signalLabel } from "../../renderer/src/features/corpus/signal-copy";

const readRepoFile = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

/**
 * 推导信号的跨语言契约。
 *
 * 后端在 `app/corpus/candidates.py` 声明 `Signal`，前端在 `signal-copy.ts` 里再写一份
 * 中文名 —— 两个进程、两种语言。后端加一个信号而前端没跟上时，界面上会出现一个英文
 * 枚举名，而那正是这套"信号 → 证据"设计最不该出的错：用户看不懂的字段等于没有依据。
 */
describe("推导信号：前端文案与后端枚举逐字对齐", () => {
  const source = readRepoFile("../../../backend/app/corpus/candidates.py");
  const block = source.match(/class Signal\(str, Enum\):([\s\S]*?)\n\n\n/);
  const signals = [...(block?.[1] ?? "").matchAll(/^\s{4}[A-Z_]+ = "([a-z_]+)"$/gm)].map(
    (match) => match[1],
  );

  it("扫描到了后端的全部信号（守卫扫描本身有效）", () => {
    expect(signals.length).toBeGreaterThanOrEqual(6);
  });

  it("两端取值集合一致：既没有漏文案的，也没有前端自造的", () => {
    expect(Object.keys(SIGNAL_LABELS).sort()).toEqual([...signals].sort());
  });

  it("不认识的取值原样显示，而不是套一个错的中文名", () => {
    expect(signalLabel("brand_new_signal")).toBe("brand_new_signal");
    expect(signalLabel("seed")).toBe("种子表");
  });
});
