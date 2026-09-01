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
  start: (input: { requestId: string; sessionId: string; userMessage: string }) => void;
  attachSubscription: (subscription: StreamSubscription) => void;
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
};

function mergeCitations(current: Citation[], incoming: Citation[]) {
  const known = new Set(current.map((citation) => `${citation.sourceId}:${citation.chunkId}`));
  return incoming.reduce<Citation[]>(
    (result, citation) => {
      const key = `${citation.sourceId}:${citation.chunkId}`;
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
  start: ({ requestId, sessionId, userMessage }) =>
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
    }),
  attachSubscription: (subscription) => {
    if (get().requestId === subscription.requestId) set({ subscription });
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
      set({ ...initialState, lastSequence: event.sequence });
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
