import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryView } from "../../renderer/src/features/memory/MemoryView";
import { installDocMindApi, repository } from "./test-docmind-api";

describe("memory management", () => {
  it("filters memory by repository", async () => {
    const list = vi.fn().mockResolvedValue({ items: [], nextCursor: null });
    installDocMindApi({ memory: { list } });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryView />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const option = await screen.findByRole("option", { name: repository.name });
    expect(list).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText("记忆知识库"), option);
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith({ repositoryIds: [repository.id], kind: undefined }),
    );
  });

  it("does not request memory until a delayed repository option is selected", async () => {
    let resolveRepositories!: (repositories: (typeof repository)[]) => void;
    const repositories = new Promise<(typeof repository)[]>((resolve) => {
      resolveRepositories = resolve;
    });
    const list = vi.fn().mockResolvedValue({ items: [], nextCursor: null });
    installDocMindApi({
      repositories: { list: vi.fn().mockReturnValue(repositories) },
      memory: { list },
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryView />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    expect(screen.getByLabelText("记忆知识库")).toHaveValue([]);
    expect(screen.queryByRole("option", { name: repository.name })).not.toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();

    await act(async () => resolveRepositories([repository]));
    const option = await screen.findByRole("option", { name: repository.name });
    expect(screen.getByLabelText("记忆知识库")).toHaveValue([]);
    expect(list).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText("记忆知识库"), option);
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith({ repositoryIds: [repository.id], kind: undefined }),
    );
    expect(list).toHaveBeenCalledTimes(1);
  });
});
