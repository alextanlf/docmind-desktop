import { MessageSquarePlus } from "lucide-react";
import type { SessionSummary } from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";
import { useCreateSessionMutation, useSessionsQuery } from "./chat.queries";

type SessionListProps = {
  onSessionSelect?: (session: SessionSummary) => void;
  selectedRepositoryIds: string[];
  selectedSessionId?: string | null;
};

function sessionGroup(session: SessionSummary) {
  const date = new Date(session.updatedAt);
  const today = new Date();
  const day = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (target === day) return "今天";
  if (target === day - 86_400_000) return "昨天";
  return date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
}

export function SessionList({
  onSessionSelect,
  selectedRepositoryIds,
  selectedSessionId,
}: SessionListProps) {
  const sessions = useSessionsQuery();
  const createSession = useCreateSessionMutation();
  const grouped = (sessions.data ?? []).reduce<Record<string, SessionSummary[]>>(
    (result, session) => {
      const group = sessionGroup(session);
      (result[group] ??= []).push(session);
      return result;
    },
    {},
  );

  const create = async () => {
    if (!selectedRepositoryIds.length) return;
    try {
      const session = await createSession.mutateAsync(selectedRepositoryIds);
      onSessionSelect?.(session);
    } catch {
      // The mutation error is rendered below in Chinese for the user.
    }
  };

  return (
    <section className="session-list" aria-label="会话列表">
      <button
        aria-label="新建会话"
        className="new-chat-button"
        disabled={!selectedRepositoryIds.length || createSession.isPending}
        onClick={() => void create()}
        title="新建会话"
        type="button"
      >
        <MessageSquarePlus aria-hidden="true" size={17} />
        <span>新建会话</span>
      </button>
      {createSession.isError ? (
        <p className="session-error">{clientErrorMessage(createSession.error)}</p>
      ) : null}
      {sessions.isPending ? <p className="session-empty">正在读取会话…</p> : null}
      {sessions.isError ? <p className="session-error">无法读取会话，请稍后重试。</p> : null}
      {Object.entries(grouped).map(([group, items]) => (
        <div className="session-group" key={group}>
          <span>{group}</span>
          {items.map((session) => (
            <button
              aria-current={selectedSessionId === session.id ? "page" : undefined}
              className={selectedSessionId === session.id ? "is-active" : undefined}
              key={session.id}
              onClick={() => onSessionSelect?.(session)}
              type="button"
            >
              {session.title}
            </button>
          ))}
        </div>
      ))}
      {!sessions.isPending && !sessions.isError && sessions.data?.length === 0 ? (
        <p className="session-empty">暂无会话</p>
      ) : null}
    </section>
  );
}
