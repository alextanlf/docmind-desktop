import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CrawlRejectionReasonSchema } from "../../shared/contracts";
import { REJECTION_COPY } from "../../renderer/src/features/imports/rejection-copy";

const readRepoFile = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

/**
 * 拒绝原因的跨语言契约。
 *
 * 后端在 `backend/app/schemas/batches.py` 声明 `CrawlRejectionReason`，前端在
 * `shared/contracts.ts` 再声明一份 —— 两个进程、两种语言，没有共享类型可用。
 * 两边各守一半：后端有测试断言"映射产生的每个值都在声明集合里"，这里断言
 * "两份声明逐字一致"和"每个取值都有给用户看的文案"。
 *
 * 🔴 少守任何一半都会退化成"前端拿到一个不认识的字符串"，而那正是这套分类
 * 想避免的事：把五种处置方式不同的失败混成一种。
 */
describe("抓取拒绝原因：前端与后端逐字对齐", () => {
  const frontend = [...CrawlRejectionReasonSchema.options];
  const source = readRepoFile("../../../backend/app/schemas/batches.py");
  const literal = source.match(/CrawlRejectionReason = Literal\[([\s\S]*?)\]/);
  const backend = [...(literal?.[1] ?? "").matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);

  it("取值集合完全一致", () => {
    expect(backend.length).toBeGreaterThan(5);
    expect(new Set(frontend)).toEqual(new Set(backend));
  });

  it("声明顺序也一致，因为前端拿它当分组的稳定次级排序", () => {
    // 顺序不一致也能跑，但"每次渲染排列都一样"就没有依据了 —— 这条顺序是刻意的。
    expect(frontend).toEqual(backend);
  });

  it.each([...CrawlRejectionReasonSchema.options])(
    "%s 有文案，也说明了下一步该做什么",
    (reason) => {
      const copy = REJECTION_COPY[reason];
      expect(copy.label.length).toBeGreaterThan(0);
      expect(copy.hint.length).toBeGreaterThan(0);
    },
  );
});
