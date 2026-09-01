import type { Citation, Message } from "../../../../shared/contracts";
import { MarkdownMessage } from "./MarkdownMessage";

type MessageListProps = {
  citations: Citation[];
  draftAssistant: string;
  draftUserMessage: string;
  messages: Message[];
};

export function MessageList({
  citations,
  draftAssistant,
  draftUserMessage,
  messages,
}: MessageListProps) {
  if (messages.length === 0 && !draftAssistant && !draftUserMessage) {
    return <div className="chat-empty">开始新的对话</div>;
  }
  return (
    <div className="message-list" aria-live="polite">
      {messages.map((message) => (
        <article className={`chat-message chat-message-${message.role}`} key={message.id}>
          <span className="message-role">{message.role === "user" ? "你" : "DocMind"}</span>
          {message.role === "assistant" ? (
            <MarkdownMessage citations={message.citations} content={message.content} />
          ) : (
            <p>{message.content}</p>
          )}
        </article>
      ))}
      {draftUserMessage ? (
        <article className="chat-message chat-message-user">
          <span className="message-role">你</span>
          <p>{draftUserMessage}</p>
        </article>
      ) : null}
      {draftAssistant ? (
        <article className="chat-message chat-message-assistant">
          <span className="message-role">DocMind</span>
          <MarkdownMessage citations={citations} content={draftAssistant} />
        </article>
      ) : null}
    </div>
  );
}
