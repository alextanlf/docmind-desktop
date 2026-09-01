import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { Workspace } from "../../renderer/src/app/Workspace";
import { ChatPanel } from "../../renderer/src/features/chat/ChatPanel";
import { useChatStreamStore } from "../../renderer/src/stores/chat-stream-store";
import {
  citation,
  installChatStreamMock,
  installDocMindApi,
  repository,
  session,
} from "./test-docmind-api";

function sendMessage(message: string) {
  fireEvent.change(screen.getByLabelText("输入问题"), { target: { value: message } });
  fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
}

describe("流式对话", () => {
  beforeEach(() => {
    appQueryClient.clear();
    useChatStreamStore.getState().reset();
  });

  it("assembles ordered deltas once and finalizes the assistant message", async () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(
      <AppProviders>
        <ChatPanel
          sessionId="00000000-0000-0000-0000-000000000025"
          repositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    sendMessage("@State 是什么？");
    act(() => {
      stream.emit({
        requestId: stream.requestId,
        type: "delta",
        sequence: 1,
        payload: { content: "@State " },
      });
      stream.emit({
        requestId: stream.requestId,
        type: "delta",
        sequence: 1,
        payload: { content: "重复" },
      });
      stream.emit({
        requestId: stream.requestId,
        type: "delta",
        sequence: 2,
        payload: { content: "用于状态。" },
      });
    });
    expect(screen.getByText("@State 用于状态。")).toBeVisible();
    act(() => {
      stream.emit({
        requestId: stream.requestId,
        type: "done",
        sequence: 3,
        payload: { messageId: "00000000-0000-0000-0000-000000000027" },
      });
    });

    await waitFor(() => expect(screen.getByText("@State 用于状态。")).toBeVisible());
    expect(stream.cancel).not.toHaveBeenCalled();
  });

  it("stops the matching stream only when the user explicitly requests it", async () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(
      <AppProviders>
        <ChatPanel
          sessionId="00000000-0000-0000-0000-000000000025"
          repositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    sendMessage("停止测试");
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    expect(screen.getByText("已停止生成")).toBeVisible();
    expect(stream.cancel).toHaveBeenCalledOnce();
  });

  it("detaches its listener on unmount without cancelling the backend stream", () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    const view = render(
      <AppProviders>
        <ChatPanel
          sessionId="00000000-0000-0000-0000-000000000025"
          repositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    sendMessage("卸载时保留生成");
    view.unmount();

    expect(stream.detach).toHaveBeenCalledOnce();
    expect(stream.cancel).not.toHaveBeenCalled();
  });

  it("cancels the active request when the user switches sessions", async () => {
    const secondSession = {
      id: "00000000-0000-0000-0000-000000000028",
      title: "另一个问题",
      repositoryIds: [repository.id],
      createdAt: "2026-08-30T08:00:00Z",
      updatedAt: "2026-08-30T08:00:00Z",
    };
    const api = installDocMindApi({
      chat: { listSessions: vi.fn().mockResolvedValue([session, secondSession]) },
    });
    const stream = installChatStreamMock(api.chat);
    render(
      <AppProviders>
        <Workspace />
      </AppProviders>,
    );

    fireEvent.click(await screen.findByRole("button", { name: session.title }));
    sendMessage("切换会话时取消");
    fireEvent.click(screen.getByRole("button", { name: secondSession.title }));

    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(screen.getByText("开始新的对话")).toBeVisible();
  });

  it("keeps the partial answer and restores the original question for retry after an error", async () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(
      <AppProviders>
        <ChatPanel
          sessionId="00000000-0000-0000-0000-000000000025"
          repositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    sendMessage("网络错误时重试");
    act(() => {
      stream.emit({
        requestId: stream.requestId,
        type: "delta",
        sequence: 1,
        payload: { content: "部分回答" },
      });
      stream.emit({
        requestId: stream.requestId,
        type: "error",
        sequence: 2,
        payload: { code: "MODEL_TIMEOUT", message: "超时", retryable: true },
      });
    });

    expect(screen.getByText("部分回答")).toBeVisible();
    expect(screen.getByText("模型连接超时，请检查网络或调大超时时间")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重新编辑问题" }));
    expect(screen.getByLabelText("输入问题")).toHaveValue("网络错误时重试");
  });

  it("renders citation controls only for structured citation IDs", async () => {
    const api = installDocMindApi();
    const stream = installChatStreamMock(api.chat);
    render(
      <AppProviders>
        <ChatPanel
          sessionId="00000000-0000-0000-0000-000000000025"
          repositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    sendMessage("引用测试");
    act(() => {
      stream.emit({
        requestId: stream.requestId,
        type: "citations",
        sequence: 1,
        payload: { citations: [citation] },
      });
      stream.emit({
        requestId: stream.requestId,
        type: "delta",
        sequence: 2,
        payload: { content: "答案 [S1] 与 [未知]" },
      });
    });

    expect(screen.getByRole("button", { name: "查看引用 S1" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看引用 未知" })).not.toBeInTheDocument();
    expect(screen.getByText(/与 \[未知\]/)).toBeVisible();
  });
});
