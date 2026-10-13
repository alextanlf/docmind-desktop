import { describe, expect, it } from "vitest";
import type { DocumentSummary } from "../../shared/contracts";
import {
  buildDocumentTree,
  documentsUnder,
  hasDirectories,
  selectionState,
} from "../../renderer/src/features/repositories/documentTree";

function doc(id: string, title: string, path: string[] = []): DocumentSummary {
  return {
    id,
    repositoryId: "00000000-0000-0000-0000-000000000021",
    remoteId: `remote-${id}`,
    title,
    remoteUrl: null,
    path,
    chunkCount: 1,
    status: "已同步",
    remoteDeleted: false,
    createdAt: "2026-08-31T00:00:00Z",
    updatedAt: "2026-08-31T00:00:00Z",
  };
}

describe("目录树", () => {
  it("groups documents by their remote path without inventing levels", () => {
    const tree = buildDocumentTree([
      doc("1", "甲", ["第一章", "第一节"]),
      doc("2", "乙", ["第一章"]),
      doc("3", "丙"),
    ]);

    expect(tree.directories.map((node) => node.name)).toEqual(["第一章"]);
    const first = tree.directories[0];
    expect(first.path).toEqual(["第一章"]);
    expect(first.documents.map((item) => item.title)).toEqual(["乙"]);
    expect(first.directories[0].path).toEqual(["第一章", "第一节"]);
    expect(first.total).toBe(2);
    expect(tree.total).toBe(3);
  });

  it("keeps every document when the provider reports no paths at all", () => {
    // provider 不提供层级时 path 全是空数组：全部落在根下，界面照旧平铺。
    const documents = [doc("1", "甲"), doc("2", "乙")];

    const tree = buildDocumentTree(documents);

    expect(hasDirectories(documents)).toBe(false);
    expect(tree.directories).toEqual([]);
    expect(tree.documents.map((item) => item.title)).toEqual(["甲", "乙"]);
    expect(tree.total).toBe(2);
  });

  it("reports a partially selected directory as partial, not as selected", () => {
    // 只有部分子文档被选中时方框显示"全选"是危险的：用户会以为整个目录都会被移除。
    const tree = buildDocumentTree([doc("1", "甲", ["第一章"]), doc("2", "乙", ["第一章"])]);
    const first = tree.directories[0];

    expect(selectionState(first, new Set())).toBe("none");
    expect(selectionState(first, new Set(["1"]))).toBe("some");
    expect(selectionState(first, new Set(["1", "2"]))).toBe("all");
  });

  it("counts nested documents when a parent directory is removed", () => {
    // 按目录移除必须把子目录里的也算上 —— 少算等于"清理过了"却还有残留。
    const tree = buildDocumentTree([
      doc("1", "甲", ["第一章", "第一节"]),
      doc("2", "乙", ["第一章", "第二节"]),
      doc("3", "丙"),
    ]);

    // 遍历顺序不设断言（目录按拼音排序），要守的是"一篇都不许漏"。
    const ids = (items: DocumentSummary[]) => items.map((item) => item.id).sort();
    expect(ids(documentsUnder(tree.directories[0]))).toEqual(["1", "2"]);
    expect(ids(documentsUnder(tree))).toEqual(["1", "2", "3"]);
  });
});
