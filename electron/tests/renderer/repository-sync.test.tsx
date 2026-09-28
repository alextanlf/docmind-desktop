import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryTree } from "../../renderer/src/features/repositories/RepositoryTree";
import {
  document,
  feishuProviderSummary,
  installDocMindApi,
  repository,
} from "./test-docmind-api";

describe("repository sync", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("shows a remote-deleted badge and triggers sync", async () => {
    const api = installDocMindApi({
      repositories: { list: vi.fn().mockResolvedValue([repository]) },
      documents: {
        list: vi.fn().mockResolvedValue([{ ...document, remoteDeleted: true }]),
      },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <RepositoryTree />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: `展开 ${repository.name}` }));
    expect(await screen.findByText("远端已删除")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "立即同步" }));
    await waitFor(() => expect(api.sync.trigger).toHaveBeenCalledWith(repository.id));
  });

  it("saves the Feishu parent node token used by future writes", async () => {
    const feishuRepository = {
      ...repository,
      id: "00000000-0000-0000-0000-000000000099",
      provider: "feishu",
      remoteId: "sp1",
      remoteUrl: null,
      remoteParentId: null,
    };
    const api = installDocMindApi({
      repositories: {
        list: vi.fn().mockResolvedValue([feishuRepository]),
        update: vi.fn().mockResolvedValue({
          ...feishuRepository,
          remoteParentId: "wikParent",
        }),
      },
      documents: { list: vi.fn().mockResolvedValue([]) },
      remote: { listProviders: vi.fn().mockResolvedValue([feishuProviderSummary]) },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <RepositoryTree />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: `展开 ${feishuRepository.name}` }));
    const parentInput = await screen.findByLabelText("写入目录");
    fireEvent.change(parentInput, { target: { value: "wikParent" } });
    fireEvent.click(screen.getByRole("button", { name: "保存目录" }));

    await waitFor(() =>
      expect(api.repositories.update).toHaveBeenCalledWith(feishuRepository.id, {
        remoteParentId: "wikParent",
      }),
    );
  });

  it("keeps a local knowledge base free of Yuque sync controls", async () => {
    const localRepository = { ...repository, provider: null, remoteId: null, remoteUrl: null };
    installDocMindApi({
      repositories: { list: vi.fn().mockResolvedValue([localRepository]) },
      documents: { list: vi.fn().mockResolvedValue([]) },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <RepositoryTree />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("本地 · 1")).toBeVisible();
    expect(screen.queryByRole("button", { name: "立即同步" })).not.toBeInTheDocument();
  });
});
