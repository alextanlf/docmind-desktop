import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentSummary, RemovedDocument } from "../../shared/contracts";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryDocuments } from "../../renderer/src/features/repositories/RepositoryDocuments";
import { installDocMindApi, repository } from "./test-docmind-api";

function summary(
  id: string,
  title: string,
  remoteId: string | null,
  path: string[] = [],
): DocumentSummary {
  return {
    id,
    repositoryId: repository.id,
    remoteId,
    title,
    path,
    remoteUrl: null,
    chunkCount: 1,
    status: "已同步",
    remoteDeleted: false,
    createdAt: "2026-08-31T00:00:00Z",
    updatedAt: "2026-08-31T00:00:00Z",
  };
}

const remoteDoc = summary("00000000-0000-4000-8000-000000000101", "远端甲", "remote-1");
const remoteDocTwo = summary("00000000-0000-4000-8000-000000000102", "远端乙", "remote-2");
const localDoc = summary("00000000-0000-4000-8000-000000000103", "本地丙", null);

const removed: RemovedDocument = {
  id: "00000000-0000-4000-8000-000000000201",
  repositoryId: repository.id,
  remoteId: "remote-9",
  title: "被移出的丁",
  removedAt: "2026-08-31T10:00:00Z",
};

function renderDocuments() {
  return render(
    <AppProviders>
      <RepositoryDocuments repositoryId={repository.id} repositoryName={repository.name} />
    </AppProviders>,
  );
}

describe("文档多选移除", () => {
  beforeEach(() => appQueryClient.clear());

  it("removes only the selected documents and tells the backend once", async () => {
    const api = installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, remoteDocTwo, localDoc]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText(`选择 ${remoteDoc.title}`));
    fireEvent.click(screen.getByLabelText(`选择 ${remoteDocTwo.title}`));
    fireEvent.click(screen.getByRole("button", { name: "移除所选的 2 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "移除 2 篇" }));

    // 一次请求带走两篇：逐篇发请求在几十篇时会在中途失败，留下"移除了一半"。
    await waitFor(() =>
      expect(api.documents.removeMany).toHaveBeenCalledWith(repository.id, [
        remoteDoc.id,
        remoteDocTwo.id,
      ]),
    );
  });

  it("warns separately about local documents, which have no way back", async () => {
    // 远端文档可以恢复，本地文档不能；混在一批里只报一个总数就是在骗人。
    installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, localDoc]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText("全选文档"));
    fireEvent.click(screen.getByRole("button", { name: "移除所选的 2 篇文档" }));

    expect(screen.getByText(/远端原文会保留，之后可以在「已移除的文档」里恢复/)).toBeVisible();
    expect(screen.getByText(/1 篇是本地文档，移除后无法恢复/)).toBeVisible();
  });

  it("does not let a stale selection act on documents that are already gone", async () => {
    // 选中后列表刷新（例如刚同步过），选中的 id 可能已经不存在了 —— 不能把无效
    // id 发给后端，也不能让计数骗人。
    const api = installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, remoteDocTwo]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText(`选择 ${remoteDoc.title}`));
    fireEvent.click(screen.getByLabelText(`选择 ${remoteDocTwo.title}`));
    appQueryClient.setQueryData(["repositories", repository.id, "documents"], [remoteDoc]);
    fireEvent.click(screen.getByRole("button", { name: "移除所选的 2 篇文档" }));

    // 只剩一篇还真实存在，弹窗与请求都必须只认它。
    expect(screen.getByRole("heading", { name: "移除 1 篇文档" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "移除 1 篇" }));
    await waitFor(() =>
      expect(api.documents.removeMany).toHaveBeenCalledWith(repository.id, [remoteDoc.id]),
    );
  });
});

describe("已移除文档的恢复", () => {
  beforeEach(() => appQueryClient.clear());

  it("restores and then syncs, because restoring alone brings nothing back", async () => {
    const api = installDocMindApi({
      documents: {
        list: vi.fn().mockResolvedValue([]),
        listRemoved: vi.fn().mockResolvedValue([removed]),
      },
    });
    renderDocuments();

    fireEvent.click(await screen.findByRole("button", { name: `恢复 ${removed.title}` }));

    await waitFor(() =>
      expect(api.documents.restoreRemoved).toHaveBeenCalledWith(repository.id, removed.id),
    );
    // 后端只负责"允许它回来"（删移出记录 + 清陈旧同步快照），内容要重新下载并
    // 重建索引。少了这一步，用户会觉得恢复按钮没反应。
    await waitFor(() => expect(api.sync.trigger).toHaveBeenCalledWith(repository.id));
  });

  it("says nothing about removed documents when there are none", async () => {
    installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc]) },
    });
    renderDocuments();

    await screen.findByLabelText(`选择 ${remoteDoc.title}`);
    expect(screen.queryByText("已移除的文档")).toBeNull();
  });
});

describe("按目录移除", () => {
  beforeEach(() => appQueryClient.clear());

  it("lets a directory be selected as a whole and removes its entire subtree", async () => {
    // 远端一直在更新，用户处理完一个目录就想整目录挪走 —— 逐个勾选在几十篇时不可用。
    // 勾目录 = 勾中它子树里的全部文档（含更深一层），再走已有的批量端点。
    const chapter = summary("00000000-0000-4000-8000-000000000301", "章内甲", "remote-a", [
      "第一章",
    ]);
    const nested = summary("00000000-0000-4000-8000-000000000302", "节内乙", "remote-b", [
      "第一章",
      "第一节",
    ]);
    const other = summary("00000000-0000-4000-8000-000000000303", "别处丙", "remote-c", ["第二章"]);
    const api = installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([chapter, nested, other]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText("选择目录 第一章"));

    // 计数必须把更深一层的算进来，否则"清理过了"却还有残留。
    expect(screen.getByRole("button", { name: "移除所选的 2 篇文档" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "移除所选的 2 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "移除 2 篇" }));

    await waitFor(() =>
      expect(api.documents.removeMany).toHaveBeenCalledWith(repository.id, [chapter.id, nested.id]),
    );
  });

  it("shows a partially selected directory as partial, never as fully selected", async () => {
    // 目录显示"全选"而实际只选了里面一篇，用户会以为整个目录都会被移除。
    const first = summary("00000000-0000-4000-8000-000000000401", "甲", "r1", ["第一章"]);
    const second = summary("00000000-0000-4000-8000-000000000402", "乙", "r2", ["第一章"]);
    installDocMindApi({ documents: { list: vi.fn().mockResolvedValue([first, second]) } });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText("选择 甲"));

    const directory = screen.getByLabelText("选择目录 第一章") as HTMLInputElement;
    expect(directory.indeterminate).toBe(true);
    expect(directory.checked).toBe(false);
    expect(screen.getByText(/已选 1/)).toBeVisible();
  });

  it("keeps the flat list when the provider reports no directories", async () => {
    // 不提供层级的 provider：不能自己编一层出来。
    const api = installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, remoteDocTwo]) },
    });
    renderDocuments();

    await screen.findByLabelText(`选择 ${remoteDoc.title}`);
    expect(screen.queryByLabelText(/选择目录/)).toBeNull();
    fireEvent.click(screen.getByLabelText(`选择 ${remoteDoc.title}`));
    fireEvent.click(screen.getByRole("button", { name: "移除所选的 1 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "移除 1 篇" }));
    await waitFor(() =>
      expect(api.documents.removeMany).toHaveBeenCalledWith(repository.id, [remoteDoc.id]),
    );
  });
});
