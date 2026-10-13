import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "../../shared/contracts";
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

function renderDocuments() {
  return render(
    <AppProviders>
      <RepositoryDocuments
        remoteRenameSupported
        repositoryId={repository.id}
        repositoryName={repository.name}
      />
    </AppProviders>,
  );
}

describe("文档多选归档", () => {
  beforeEach(() => appQueryClient.clear());

  it("archives only the selected documents and tells the backend once", async () => {
    const api = installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, remoteDocTwo, localDoc]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText(`选择 ${remoteDoc.title}`));
    fireEvent.click(screen.getByLabelText(`选择 ${remoteDocTwo.title}`));
    fireEvent.click(screen.getByRole("button", { name: "归档所选的 2 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "归档 2 篇" }));

    // 一次请求带走两篇：逐篇发请求在几十篇时会在中途失败，留下"归档了一半"。
    await waitFor(() =>
      expect(api.documents.archiveMany).toHaveBeenCalledWith(repository.id, [
        remoteDoc.id,
        remoteDocTwo.id,
      ]),
    );
  });

  it("does not ask the user to retype the title, and promises it can come back", async () => {
    // 归档可撤销：内容、向量、版本历史、图谱都还在，恢复是即时的。重确认留给
    // 不可撤销的那件事（设置页里的永久删除），摩擦与不可逆性成比例。
    installDocMindApi({
      documents: { list: vi.fn().mockResolvedValue([remoteDoc, localDoc]) },
    });
    renderDocuments();

    fireEvent.click(await screen.findByLabelText("全选文档"));
    fireEvent.click(screen.getByRole("button", { name: "归档所选的 2 篇文档" }));

    expect(screen.queryByLabelText("输入文档标题以确认")).toBeNull();
    expect(screen.getByRole("button", { name: "归档 2 篇" })).toBeEnabled();
    // 本地文档与远端文档在归档这件事上没有区别：两者都能在设置页恢复。
    expect(screen.getByText(/设置 →\s*已归档的文档/)).toBeVisible();
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
    fireEvent.click(screen.getByRole("button", { name: "归档所选的 2 篇文档" }));

    // 只剩一篇还真实存在，弹窗与请求都必须只认它。
    expect(screen.getByRole("heading", { name: "归档文档" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() =>
      expect(api.documents.archiveMany).toHaveBeenCalledWith(repository.id, [remoteDoc.id]),
    );
  });
});

describe("按目录归档", () => {
  beforeEach(() => appQueryClient.clear());

  it("lets a directory be selected as a whole and archives its entire subtree", async () => {
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
    expect(screen.getByRole("button", { name: "归档所选的 2 篇文档" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "归档所选的 2 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "归档 2 篇" }));

    await waitFor(() =>
      expect(api.documents.archiveMany).toHaveBeenCalledWith(repository.id, [
        chapter.id,
        nested.id,
      ]),
    );
  });

  it("shows a partially selected directory as partial, never as fully selected", async () => {
    // 目录显示"全选"而实际只选了里面一篇，用户会以为整个目录都会被归档。
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
    fireEvent.click(screen.getByRole("button", { name: "归档所选的 1 篇文档" }));
    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() =>
      expect(api.documents.archiveMany).toHaveBeenCalledWith(repository.id, [remoteDoc.id]),
    );
  });
});
