import { Globe, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { EventEnvelope } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { useChatStreamStore } from "../../stores/chat-stream-store";
import { chatKeys, useMessagesQuery } from "./chat.queries";
import { MessageComposer } from "./MessageComposer";
import { MessageList } from "./MessageList";

function createRequestId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
    return crypto.randomUUID();
  return "00000000-0000-4000-8000-000000000029";
}

export function ChatPanel({
  sessionId,
  repositoryIds,
  ended = false,
}: {
  sessionId: string;
  repositoryIds: string[];
  ended?: boolean;
}) {
  const queryClient = useQueryClient();
  const messages = useMessagesQuery(sessionId);
  const stream = useChatStreamStore();
  const [composerValue, setComposerValue] = useState("");
  const activeStream = stream.sessionId === sessionId ? stream : null;
  const handleEvent = useCallback(
    (event: EventEnvelope) => {
      const accepted = useChatStreamStore.getState().applyEvent(event);
      if (accepted && event.type === "done") {
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: chatKeys.messages(sessionId) }),
          queryClient.invalidateQueries({ queryKey: chatKeys.sessions }),
        ]);
      }
    },
    [queryClient, sessionId],
  );
  useEffect(() => {
    if (
      !activeStream?.requestId ||
      activeStream.status !== "streaming" ||
      activeStream.subscription
    )
      return;
    const subscription =
      activeStream.streamMode === "search" && activeStream.continuationUserMessageId
        ? window.docmind.chat.searchStream(
            {
              requestId: activeStream.requestId,
              sessionId,
              userMessageId: activeStream.continuationUserMessageId,
              repositoryIds,
            },
            handleEvent,
            activeStream.lastSequence,
          )
        : window.docmind.chat.stream(
            {
              requestId: activeStream.requestId,
              sessionId,
              message: activeStream.userMessage,
              repositoryIds,
            },
            handleEvent,
            activeStream.lastSequence,
          );
    useChatStreamStore.getState().attachSubscription(subscription);
  }, [activeStream, handleEvent, repositoryIds, sessionId]);
  useEffect(
    () => () => {
      const current = useChatStreamStore.getState();
      if (current.sessionId === sessionId && current.status === "streaming") {
        current.subscription?.detach?.();
        current.detachForSession(sessionId);
      }
    },
    [sessionId],
  );
  const canSend = !ended && repositoryIds.length > 0 && activeStream?.status !== "streaming";

  const send = () => {
    const message = composerValue.trim();
    if (!message || !canSend) return;
    const requestId = createRequestId();
    useChatStreamStore.getState().start({ requestId, sessionId, userMessage: message });
    setComposerValue("");
    const subscription = window.docmind.chat.stream(
      { requestId, sessionId, message, repositoryIds },
      handleEvent,
    );
    useChatStreamStore.getState().attachSubscription(subscription);
  };

  const retry = () => {
    if (!activeStream?.userMessage) return;
    if (activeStream.streamMode === "search" && activeStream.continuationUserMessageId) {
      const requestId = createRequestId();
      const userMessageId = activeStream.continuationUserMessageId;
      useChatStreamStore.getState().start({
        requestId,
        sessionId,
        userMessage: activeStream.userMessage,
        continuationUserMessageId: userMessageId,
      });
      const subscription = window.docmind.chat.searchStream(
        { requestId, sessionId, userMessageId, repositoryIds },
        handleEvent,
      );
      useChatStreamStore.getState().attachSubscription(subscription);
      return;
    }
    setComposerValue(activeStream.userMessage);
    useChatStreamStore.getState().reset();
  };
  const searchWeb = () => {
    if (!activeStream?.searchSuggestion) return;
    const requestId = createRequestId();
    const userMessageId = activeStream.searchSuggestion.userMessageId;
    useChatStreamStore.getState().start({
      requestId,
      sessionId,
      userMessage: activeStream.userMessage,
      continuationUserMessageId: userMessageId,
    });
    const subscription = window.docmind.chat.searchStream(
      { requestId, sessionId, userMessageId, repositoryIds },
      handleEvent,
    );
    useChatStreamStore.getState().attachSubscription(subscription);
  };

  const disabled = !canSend;
  return (
    <section className="chat-panel" aria-label="对话">
      <div className="chat-messages">
        {messages.isPending ? <p className="chat-loading">正在读取消息…</p> : null}
        {messages.isError ? <p className="chat-error">无法读取消息，请重新选择会话。</p> : null}
        <MessageList
          citations={activeStream?.citations ?? []}
          draftAssistant={activeStream?.draftAssistant ?? ""}
          draftUserMessage={activeStream?.userMessage ?? ""}
          messages={messages.data ?? []}
          streamRequestId={activeStream?.requestId}
        />
      </div>
      {activeStream?.status === "streaming" ? (
        <div className="chat-stream-status">
          <span>正在生成回答</span>
          <IconButton
            icon={<Square aria-hidden="true" size={14} />}
            label="停止生成"
            onClick={() => useChatStreamStore.getState().stop()}
            size="small"
          />
        </div>
      ) : null}
      {activeStream?.error ? (
        <div className="chat-stream-error" role="alert">
          <span>{activeStream.error}</span>
          {activeStream.status === "error" ? (
            <button className="button button-secondary" onClick={retry} type="button">
              {activeStream.streamMode === "search" ? "重试联网搜索" : "重新编辑问题"}
            </button>
          ) : null}
        </div>
      ) : null}
      {activeStream?.warning ? (
        <p className="chat-stream-warning" role="status">
          {activeStream.warning}
        </p>
      ) : null}
      {activeStream?.searchSuggestion ? (
        <div className="chat-search-suggestion">
          <button className="button button-secondary" onClick={searchWeb} type="button">
            <Globe aria-hidden="true" size={16} />
            联网搜索
          </button>
        </div>
      ) : null}
      {repositoryIds.length === 0 ? (
        <p className="chat-scope-guide">请先选择已建立索引的知识库，或导入文档。</p>
      ) : null}
      {ended ? <p className="chat-scope-guide">此会话已结束，只能查看历史消息。</p> : null}
      <MessageComposer
        disabled={disabled}
        onChange={setComposerValue}
        onSend={send}
        value={composerValue}
      />
    </section>
  );
}
