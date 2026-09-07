import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { ChatPanel } from "../../renderer/src/features/chat/ChatPanel";
import { useChatStreamStore } from "../../renderer/src/stores/chat-stream-store";
import { installChatStreamMock, installDocMindApi, repository } from "./test-docmind-api";

const props = { sessionId: "00000000-0000-0000-0000-000000000025", repositoryIds: [repository.id] };

describe("chat route presentation", () => {
  beforeEach(() => {
    useChatStreamStore.getState().reset();
  });

  it("renders local, cloud and fallback badges from route events", () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(<AppProviders><ChatPanel {...props} /></AppProviders>);
    fireEvent.change(screen.getByLabelText("输入问题"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    act(() => stream.emit({ requestId: stream.requestId, type: "progress", sequence: 1, payload: { route: { source: "local", model: "qwen2.5:7b", mode: "local_only" } } }));
    expect(screen.getByText("本地 · qwen2.5:7b")).toBeInTheDocument();
    act(() => stream.emit({ requestId: stream.requestId, type: "progress", sequence: 2, payload: { route: { source: "cloud", model: "deepseek-chat", mode: "automatic", fallbackReason: "OLLAMA_UNAVAILABLE" } } }));
    expect(screen.getByText("云端 · deepseek-chat（本地不可用，已回退）")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("本次回答已使用云端模型");
  });

  it("offers a settings action for a missing local model", () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(<AppProviders><ChatPanel {...props} /></AppProviders>);
    fireEvent.change(screen.getByLabelText("输入问题"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    act(() => stream.emit({ requestId: stream.requestId, type: "error", sequence: 1, payload: { code: "OLLAMA_MODEL_NOT_INSTALLED" } }));
    expect(screen.getByRole("alert")).toHaveTextContent("选定模型尚未安装");
    expect(screen.getByRole("button", { name: "打开设置并拉取模型" })).toBeInTheDocument();
  });
});
