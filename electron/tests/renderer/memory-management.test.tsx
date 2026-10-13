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
    expect(list).not.toHaveBeenCalled();
    await user.click(await screen.findByLabelText("记忆知识库"));
    await user.click(await screen.findByRole("checkbox", { name: repository.name }));
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith({ repositoryIds: [repository.id], kind: undefined }),
    );
  });

  it("keeps the refresh button disabled until a repository is selected", async () => {
    const list = vi.fn().mockResolvedValue({ items: [], nextCursor: null });
    installDocMindApi({ memory: { list } });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryView />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const refresh = screen.getByLabelText("刷新记忆");
    // 列表查询在没选知识库时是关着的（enabled: false），refetch 不会有任何请求 ——
    // 一个点了什么都不发生的按钮比一个禁用的按钮更像坏了。
    expect(refresh).toBeDisabled();
    await user.click(await screen.findByLabelText("记忆知识库"));
    await user.click(await screen.findByRole("checkbox", { name: repository.name }));
    await waitFor(() => expect(refresh).toBeEnabled());
  });

  it("does not request memory until a delayed repository is selected", async () => {
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
    // 没有选择时浮层上的 summary 显示占位文案，而不是某个知识库的名字。
    expect(screen.getByLabelText("记忆知识库")).toHaveTextContent("选择知识库");
    expect(screen.queryByRole("checkbox", { name: repository.name })).not.toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();

    await act(async () => resolveRepositories([repository]));
    await user.click(screen.getByLabelText("记忆知识库"));
    await user.click(await screen.findByRole("checkbox", { name: repository.name }));
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith({ repositoryIds: [repository.id], kind: undefined }),
    );
    expect(list).toHaveBeenCalledTimes(1);
  });
});
