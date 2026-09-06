import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatPanel } from "../../renderer/src/features/chat/ChatPanel";
import { useChatStreamStore } from "../../renderer/src/stores/chat-stream-store";
import { installDocMindApi, repository, session } from "./test-docmind-api";
describe("search permission", () => {
  it("continues an ask-mode gap without creating a second user message", async () => {
    const searchStream = vi.fn().mockReturnValue({
      requestId: "00000000-0000-0000-0000-000000000054",
      cancel: vi.fn(),
      detach: vi.fn(),
    });
    installDocMindApi({ chat: { searchStream } });
    useChatStreamStore.setState({
      requestId: null,
      sessionId: session.id,
      userMessage: "question",
      draftAssistant: "",
      citations: [],
      lastSequence: 3,
      status: "idle",
      error: null,
      subscription: null,
      searchSuggestion: { userMessageId: "00000000-0000-0000-0000-000000000053" },
      continuationUserMessageId: null,
      streamMode: "chat",
      warning: null,
    });
    const view = render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatPanel repositoryIds={[repository.id]} sessionId={session.id} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "联网搜索" }));
    expect(searchStream).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: session.id,
        userMessageId: "00000000-0000-0000-0000-000000000053",
      }),
      expect.any(Function),
    );
    view.unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatPanel repositoryIds={[repository.id]} sessionId={session.id} />
      </QueryClientProvider>,
    );
    expect(searchStream.mock.calls.length).toBeGreaterThanOrEqual(2);
    for (const [input] of searchStream.mock.calls) {
      expect(input).toEqual(
        expect.objectContaining({ userMessageId: "00000000-0000-0000-0000-000000000053" }),
      );
    }
  });
});
