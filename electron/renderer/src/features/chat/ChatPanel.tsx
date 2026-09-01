import { Square } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
}: {
  sessionId: string;
  repositoryIds: string[];
}) {
  const queryClient = useQueryClient();
  const messages = useMessagesQuery(sessionId);
  const stream = useChatStreamStore();
  const [composerValue, setComposerValue] = useState("");
  const activeStream = stream.sessionId === sessionId ? stream : null;
  const canSend = repositoryIds.length > 0 && activeStream?.status !== "streaming";

  const send = () => {
    const message = composerValue.trim();
    if (!message || !canSend) return;
    const requestId = createRequestId();
    useChatStreamStore.getState().start({ requestId, sessionId, userMessage: message });
    setComposerValue("");
    const subscription = window.docmind.chat.stream(
      { requestId, sessionId, message, repositoryIds },
      (event) => {
        const accepted = useChatStreamStore.getState().applyEvent(event);
        if (accepted && event.type === "done") {
          void Promise.all([
            queryClient.invalidateQueries({ queryKey: chatKeys.messages(sessionId) }),
            queryClient.invalidateQueries({ queryKey: chatKeys.sessions }),
          ]);
        }
      },
    );
    useChatStreamStore.getState().attachSubscription(subscription);
  };

  const retry = () => {
    if (!activeStream?.userMessage) return;
    setComposerValue(activeStream.userMessage);
    useChatStreamStore.getState().reset();
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
              重新编辑问题
            </button>
          ) : null}
        </div>
      ) : null}
      {repositoryIds.length === 0 ? (
        <p className="chat-scope-guide">请先选择已建立索引的知识库，或导入文档。</p>
      ) : null}
      <MessageComposer
        disabled={disabled}
        onChange={setComposerValue}
        onSend={send}
        value={composerValue}
      />
    </section>
  );
}
