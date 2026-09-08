import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryTree } from "../../renderer/src/features/repositories/RepositoryTree";
import { document, installDocMindApi, repository } from "./test-docmind-api";

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
});
