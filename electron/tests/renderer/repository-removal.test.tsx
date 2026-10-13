import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RemovedRepository } from "../../shared/contracts";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryTree } from "../../renderer/src/features/repositories/RepositoryTree";
import { openRowMenu } from "./row-menu";
import {
  installDocMindApi,
  localRepository,
  removedRepository,
  repository,
} from "./test-docmind-api";

function renderTree() {
  return render(
    <AppProviders>
      <RepositoryTree />
    </AppProviders>,
  );
}

describe("移除知识库", () => {
  beforeEach(() => appQueryClient.clear());

  it("requires the knowledge base name before it will remove anything", async () => {
    const api = installDocMindApi();
    renderTree();

    await openRowMenu(repository.name, `移除 ${repository.name}`);
    const confirmButton = screen.getByRole("button", { name: "移除知识库" });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("输入知识库名称以确认"), {
      target: { value: "别的名字" },
    });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("输入知识库名称以确认"), {
      target: { value: repository.name },
    });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    await waitFor(() => expect(api.repositories.remove).toHaveBeenCalledWith(repository.id, true));
  });

  it("tells the user the remote is untouched only for a remote-bound knowledge base", async () => {
    installDocMindApi({ repositories: { list: async () => [localRepository] } });
    renderTree();

    await openRowMenu(localRepository.name, `移除 ${localRepository.name}`);

    // 本地库没有远端可承诺，承诺了就是假话；而"无法撤销"必须说，因为它真的没有出口。
    expect(screen.getByText(/此操作无法撤销/)).toBeVisible();
    expect(screen.queryByText(/不会被删除/)).toBeNull();
  });

  it("promises the remote survives when removing a remote-bound knowledge base", async () => {
    installDocMindApi();
    renderTree();

    await openRowMenu(repository.name, `移除 ${repository.name}`);

    expect(screen.getByText(/语雀上的知识库与文档不会被删除/)).toBeVisible();
    // 说了远端不动，就必须同时给出找回的路，否则用户不敢点。
    expect(screen.getByText(/已移除的知识库/)).toBeVisible();
  });

  it("lists removed knowledge bases with a way back and calls restore", async () => {
    // 被移除的库不该同时出现在知识库列表里 —— 那样「移除」看起来就没生效。
    const api = installDocMindApi({
      repositories: { list: async () => [], listRemoved: async () => [removedRepository] },
    });
    renderTree();

    const restoreButton = await screen.findByRole("button", {
      name: `恢复 ${removedRepository.name}`,
    });
    // 名字与来源都要在：只给一个 id 的话，用户没法判断该不该恢复。
    expect(screen.getByText(removedRepository.name)).toBeVisible();
    expect(screen.getByText("语雀")).toBeVisible();

    fireEvent.click(restoreButton);

    await waitFor(() =>
      expect(api.repositories.restore).toHaveBeenCalledWith(removedRepository.id),
    );
  });

  it("says nothing about removed knowledge bases when there are none", async () => {
    installDocMindApi();
    renderTree();

    await screen.findByRole("button", { name: `${repository.name} 的操作` });
    expect(screen.queryByText("已移除的知识库")).toBeNull();
  });

  it("shows the new removal right after removing, without a reload", async () => {
    // 「已移除」是一个独立 query，移除后不失效它就永远不会出现 —— 用户会以为
    // 移除没生效（远端绑定的库本来就是这个功能的痛点）。
    let removedItems: RemovedRepository[] = [];
    installDocMindApi({
      repositories: {
        list: vi.fn().mockResolvedValue([repository]),
        listRemoved: vi.fn(() => Promise.resolve(removedItems)),
        remove: vi.fn(() => {
          removedItems = [removedRepository];
          return Promise.resolve(undefined);
        }),
      },
    });
    renderTree();

    await openRowMenu(repository.name, `移除 ${repository.name}`);
    fireEvent.change(screen.getByLabelText("输入知识库名称以确认"), {
      target: { value: repository.name },
    });
    fireEvent.click(screen.getByRole("button", { name: "移除知识库" }));

    expect(
      await screen.findByRole("button", { name: `恢复 ${removedRepository.name}` }),
    ).toBeVisible();
  });
});
