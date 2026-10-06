import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CreateBatchInputSchema } from "../../shared/contracts";

const readRepoFile = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

const UUID = "00000000-0000-0000-0000-000000000001";

/**
 * 后端 `CreateBatchRequest`（backend/app/schemas/batches.py）是以 `kind` 为
 * 判别式的 union；前端 `CreateBatchInputSchema` 必须覆盖**同样的** kind 集合。
 *
 * 🔴 此前前端只写了 `staged_directory` 一种，UI 上「网站 / 远程知识库」两个
 * tab 构造的 kind 被 preload 的 `.parse()` 直接拒掉 —— 两个 tab 完全不可用，
 * 而当时没有任何测试覆盖这条链路。
 */
describe("CreateBatchInputSchema 与后端 kind 对齐", () => {
  const frontendKinds = CreateBatchInputSchema.options.map((option) => option.shape.kind.value);

  it("kind 集合与后端判别式 union 完全一致", () => {
    const source = readRepoFile("../../../backend/app/schemas/batches.py");
    // 判别式 union 的成员，形如 `kind: Literal["web"]`
    const backendKinds = [...source.matchAll(/kind: Literal\["(\w+)"\]/g)].map((m) => m[1]);

    expect(backendKinds.length).toBeGreaterThan(1);
    expect(new Set(frontendKinds)).toEqual(new Set(backendKinds));
  });

  it.each([
    ["staged_directory", { kind: "staged_directory", sourceId: UUID, repositoryId: UUID }],
    [
      "web",
      {
        kind: "web",
        entryUrl: "https://example.com/docs",
        repositoryId: UUID,
        maxDepth: 5,
        maxPages: 200,
        useSitemap: true,
      },
    ],
    ["remote_repository", { kind: "remote_repository", repositoryId: UUID }],
    [
      "search_results",
      {
        kind: "search_results",
        searchRunId: UUID,
        resultIds: [UUID],
        repositoryId: UUID,
      },
    ],
  ])("接受 %s 的合法负载", (_kind, payload) => {
    expect(CreateBatchInputSchema.parse(payload)).toMatchObject({ kind: _kind });
  });

  it("web 的 maxPages 超过后端上限 200 时被拒", () => {
    const result = CreateBatchInputSchema.safeParse({
      kind: "web",
      entryUrl: "https://example.com",
      repositoryId: UUID,
      maxPages: 500,
    });
    expect(result.success).toBe(false);
  });

  it("未知 kind 被拒", () => {
    expect(
      CreateBatchInputSchema.safeParse({ kind: "not_a_kind", repositoryId: UUID }).success,
    ).toBe(false);
  });
});