import { create } from "zustand";
import {
  CitationSchema,
  type Citation,
  type EventEnvelope,
  type StreamSubscription,
} from "../../../shared/contracts";

export type ChatStreamStatus = "idle" | "streaming" | "error" | "stopped";

type ChatStreamState = {
  requestId: string | null;
  sessionId: string | null;
  userMessage: string;
  draftAssistant: string;
  citations: Citation[];
  lastSequence: number;
  status: ChatStreamStatus;
  error: string | null;
  subscription: StreamSubscription | null;
  searchSuggestion: { userMessageId: string } | null;
  continuationUserMessageId: string | null;
  streamMode: "chat" | "search";
  warning: string | null;
  start: (input: { requestId: string; sessionId: string; userMessage: string; continuationUserMessageId?: string }) => void;
  attachSubscription: (subscription: StreamSubscription) => void;
  detachForSession: (sessionId: string) => void;
  applyEvent: (event: EventEnvelope) => boolean;
  stop: () => void;
  cancelForSession: (sessionId: string | null) => void;
  reset: () => void;
};

const initialState = {
  requestId: null,
  sessionId: null,
  userMessage: "",
  draftAssistant: "",
  citations: [] as Citation[],
  lastSequence: 0,
  status: "idle" as ChatStreamStatus,
  error: null,
  subscription: null,
  searchSuggestion: null,
  continuationUserMessageId: null,
  streamMode: "chat" as const,
  warning: null,
};

function mergeCitations(current: Citation[], incoming: Citation[]) {
  const identity = (citation: Citation) => `${citation.sourceId}:${citation.kind === "memory" ? citation.memoryId : citation.kind === "web" ? citation.resultId : citation.chunkId}`;
  const known = new Set(current.map(identity));
  return incoming.reduce<Citation[]>(
    (result, citation) => {
      const key = identity(citation);
      if (!known.has(key)) {
        known.add(key);
        result.push(citation);
      }
      return result;
    },
    [...current],
  );
}

function errorMessage(payload: Record<string, unknown>) {
  const error =
    payload.error && typeof payload.error === "object"
      ? (payload.error as Record<string, unknown>)
      : payload;
  const code = typeof error.code === "string" ? error.code : "";
  const messages: Record<string, string> = {
    MODEL_TIMEOUT: "模型连接超时，请检查网络或调大超时时间",
    MODEL_UNAVAILABLE: "模型服务暂不可用，请稍后重试",
    MODEL_AUTH_FAILED: "API Key 无效，请更新密钥后重试",
    RATE_LIMITED: "请求过于频繁，请稍后重试",
    BACKEND_UNAVAILABLE: "本地服务暂不可用，请稍后重试",
  };
  return messages[code] ?? "回答生成失败，请检查设置后重试";
}

export const useChatStreamStore = create<ChatStreamState>((set, get) => ({
  ...initialState,
  start: ({ requestId, sessionId, userMessage, continuationUserMessageId }) =>
    set({
      requestId,
      sessionId,
      userMessage,
      draftAssistant: "",
      citations: [],
      lastSequence: 0,
      status: "streaming",
      error: null,
      subscription: null,
      searchSuggestion: null,
      continuationUserMessageId: continuationUserMessageId ?? null,
      streamMode: continuationUserMessageId ? "search" : "chat",
      warning: null,
    }),
  attachSubscription: (subscription) => {
    if (get().requestId === subscription.requestId) set({ subscription });
  },
  detachForSession: (sessionId) => {
    const current = get();
    if (current.sessionId === sessionId && current.status === "streaming")
      set({ subscription: null });
  },
  applyEvent: (event) => {
    const current = get();
    if (event.requestId !== current.requestId || event.sequence <= current.lastSequence)
      return false;

    if (event.type === "delta") {
      const content = typeof event.payload.content === "string" ? event.payload.content : "";
      set((state) => ({
        draftAssistant: state.draftAssistant + content,
        lastSequence: event.sequence,
      }));
      return true;
    }
    if (event.type === "citations") {
      const parsed = CitationSchema.array().safeParse(event.payload.citations);
      set((state) => ({
        citations: parsed.success ? mergeCitations(state.citations, parsed.data) : state.citations,
        lastSequence: event.sequence,
      }));
      return true;
    }
    if (event.type === "done") {
      const suggested = event.payload.searchSuggested === true && typeof event.payload.userMessageId === "string" ? { userMessageId: event.payload.userMessageId } : null;
      const rawWarning = event.payload.warning;
      const warning = typeof rawWarning === "string"
        ? rawWarning
        : rawWarning && typeof rawWarning === "object" && typeof (rawWarning as Record<string, unknown>).message === "string"
          ? (rawWarning as Record<string, string>).message
          : null;
      set({ ...initialState, sessionId: current.sessionId, userMessage: suggested ? current.userMessage : "", searchSuggestion: suggested, warning, lastSequence: event.sequence });
      return true;
    }
    if (event.type === "error") {
      set((state) => ({
        status: "error",
        error: errorMessage(event.payload),
        lastSequence: event.sequence,
        subscription: null,
        requestId: state.requestId,
      }));
      return true;
    }
    set({ lastSequence: event.sequence });
    return true;
  },
  stop: () => {
    const current = get();
    if (current.status !== "streaming" || !current.subscription) return;
    current.subscription.cancel();
    set({ requestId: null, subscription: null, status: "stopped", error: "已停止生成" });
  },
  cancelForSession: (sessionId) => {
    const current = get();
    if (!sessionId || current.sessionId !== sessionId || current.status !== "streaming") return;
    current.subscription?.cancel();
    set(initialState);
  },
  reset: () => set(initialState),
}));
