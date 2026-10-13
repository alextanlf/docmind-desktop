import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "../../shared/contracts";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryDocuments } from "../../renderer/src/features/repositories/RepositoryDocuments";
import { RepositoryTree } from "../../renderer/src/features/repositories/RepositoryTree";
import { openRowMenu } from "./row-menu";
import {
  allProviderSummaries,
  document,
  installDocMindApi,
  localRepository,
  repository,
} from "./test-docmind-api";

function renderTree() {
  return render(
    <AppProviders>
      <RepositoryTree />
    </AppProviders>,
  );
}

function renderDocuments(remoteRenameSupported = true) {
  return render(
    <AppProviders>
      <RepositoryDocuments
        remoteRenameSupported={remoteRenameSupported}
        repositoryId={repository.id}
        repositoryName={repository.name}
      />
    </AppProviders>,
  );
}

function summary(id: string, title: string): DocumentSummary {
  return {
    id,
    repositoryId: repository.id,
    remoteId: `remote-${id}`,
    title,
    path: [],
    remoteUrl: null,
    chunkCount: 1,
    status: "已同步",
    remoteDeleted: false,
    createdAt: "2026-08-31T00:00:00Z",
    updatedAt: "2026-08-31T00:00:00Z",
  };
}

describe("重命名知识库", () => {
  beforeEach(() => appQueryClient.clear());

  it("renames the knowledge base and does not nag when the remote was renamed too", async () => {
    const api = installDocMindApi();
    renderTree();

    await openRowMenu(repository.name, `重命名 ${repository.name}`);
    // 初值是当前名字，用户不必重新打一遍。
    const input = screen.getByLabelText("知识库名称");
    expect(input).toHaveValue(repository.name);

    fireEvent.change(input, { target: { value: "SwiftUI 笔记" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));

    await waitFor(() =>
      expect(api.repositories.rename).toHaveBeenCalledWith(repository.id, "SwiftUI 笔记"),
    );
    // 远端也改了 => 没有什么要额外解释的，对话框直接关掉。
    await waitFor(() => expect(screen.queryByLabelText("知识库名称")).toBeNull());
    expect(screen.queryByText(/远端知识库的名字没有跟着改/)).toBeNull();
  });

  it("keeps the dialog open and says the remote was not renamed", async () => {
    installDocMindApi({
      repositories: {
        list: async () => [repository],
        rename: async (_repositoryId: string, name: string) => ({
          scope: "local_only" as const,
          repository: { ...repository, name },
        }),
      },
    });
    renderTree();

    await openRowMenu(repository.name, `重命名 ${repository.name}`);
    fireEvent.change(screen.getByLabelText("知识库名称"), {
      target: { value: "本地新名" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));

    // 名字确实改了（本地），所以不能报错；但远端那一半没成，必须说出来 ——
    // 否则下一次列表刷新把远端旧名字拉回来时，用户会以为是自己看错了。
    expect(await screen.findByText(/远端知识库的名字没有跟着改/)).toBeVisible();
    expect(screen.getByRole("button", { name: "知道了" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "保存名称" })).toBeNull();
  });

  it("warns before the fact when the source cannot rename a remote knowledge base", async () => {
    installDocMindApi({
      // 本地库：provider 为空 => 没有任何远端可改名，界面必须提前说明。
      repositories: { list: async () => [localRepository] },
    });
    renderTree();

    await openRowMenu(localRepository.name, `重命名 ${localRepository.name}`);

    // 本地库永远改不到远端，所以这句话必须在**改之前**就说，而不是改完再说。
    expect(screen.getByText(/名字只在 DocMind 里生效/)).toBeVisible();
  });

  it("refuses to submit a blank or unchanged name", async () => {
    const api = installDocMindApi();
    renderTree();

    await openRowMenu(repository.name, `重命名 ${repository.name}`);
    const save = screen.getByRole("button", { name: "保存名称" });
    // 没改过任何东西：不该发出一次什么也不改的请求。
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText("知识库名称"), { target: { value: "   " } });
    expect(save).toBeDisabled();
    expect(api.repositories.rename).not.toHaveBeenCalled();
  });

  it("surfaces a remote rename failure instead of pretending it worked", async () => {
    installDocMindApi({
      repositories: {
        list: async () => [repository],
        rename: async () => {
          throw new Error("REMOTE_OPERATION_FAILED");
        },
      },
    });
    renderTree();

    await openRowMenu(repository.name, `重命名 ${repository.name}`);
    fireEvent.change(screen.getByLabelText("知识库名称"), { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));

    expect(await screen.findByRole("alert")).toBeVisible();
    // 失败就停在原地：输入框还在，用户可以重试，而不会以为已经改好了。
    expect(screen.getByLabelText("知识库名称")).toBeVisible();
  });
});

describe("重命名文档", () => {
  beforeEach(() => appQueryClient.clear());

  it("renames a document from the list and refreshes the row", async () => {
    let title = document.title;
    const api = installDocMindApi({
      documents: {
        list: vi.fn(async () => [summary(document.id, title)]),
        rename: vi.fn(async (_documentId: string, next: string) => {
          title = next;
          return { scope: "remote" as const, document: { ...document, title: next } };
        }),
      },
    });
    renderDocuments();

    await openRowMenu(document.title, `重命名 ${document.title}`);
    expect(screen.getByLabelText("文档标题")).toHaveValue(document.title);
    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "State 管理（新）" } });
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));

    await waitFor(() =>
      expect(api.documents.rename).toHaveBeenCalledWith(document.id, "State 管理（新）"),
    );
    // 列表要跟着刷新：改名后还挂着旧标题，用户会以为改名没生效。
    expect(await screen.findByText("State 管理（新）")).toBeVisible();
    await waitFor(() => expect(screen.queryByLabelText("文档标题")).toBeNull());
  });

  it("explains a local-only document rename without closing the dialog", async () => {
    installDocMindApi({
      documents: {
        list: vi.fn(async () => [summary(document.id, document.title)]),
        rename: async (_documentId: string, next: string) => ({
          scope: "local_only" as const,
          document: { ...document, title: next },
        }),
      },
    });
    renderDocuments(false);

    await openRowMenu(document.title, `重命名 ${document.title}`);
    // 不支持只改标题的来源，改之前就要说明白：写成"编辑保存"会把正文整篇重发。
    expect(screen.getByText(/远端的标题没有跟着改|名字只在 DocMind 里生效/)).toBeVisible();

    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "本地标题" } });
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));

    expect(await screen.findByText(/远端的标题没有跟着改/)).toBeVisible();
  });
});

describe("provider 能力清单", () => {
  it("declares whether each source can rename, so the client can warn correctly", () => {
    const byName = new Map(allProviderSummaries.map((item) => [item.name, item]));

    expect(byName.get("yuque")?.capabilities.documentRename).toBe(true);
    // 飞书没有重命名 wiki space 的接口 —— 声明成 false 才能让界面提前说实话。
    expect(byName.get("feishu")?.capabilities.repositoryRename).toBe(false);
  });
});
