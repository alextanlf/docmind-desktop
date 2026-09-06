import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { Workspace } from "../../renderer/src/app/Workspace";
import { ChatPanel } from "../../renderer/src/features/chat/ChatPanel";
import { installDocMindApi, repository, session } from "./test-docmind-api";

describe("session lifecycle", () => {
  it("disables the composer for ended sessions", () => {
    installDocMindApi();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatPanel ended repositoryIds={[repository.id]} sessionId={session.id} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("textbox", { name: "输入问题" })).toBeDisabled();
  });
});

it("requires an in-app confirmation and restores focus after ending a session", async () => {
  const endSession = vi.fn().mockResolvedValue({ ...session, endedAt: "2026-09-04T00:00:00Z" });
  installDocMindApi({ memory: { endSession } });
  render(
    <AppProviders>
      <Workspace />
    </AppProviders>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: session.title }));
  await user.click(screen.getByRole("button", { name: "结束会话" }));
  expect(screen.getByRole("dialog", { name: "结束会话" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认结束会话" }));
  expect(endSession).toHaveBeenCalledWith(session.id);
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "知识蒸馏" })).toHaveFocus());
});
