import { create } from "zustand";
import {
  CitationSchema,
  type Citation,
  type EventEnvelope,
  type StreamSubscription,
} from "../../../shared/contracts";
import { clientErrorMessage } from "../lib/client-errors";

export type RouteView = {
  source: "local" | "cloud";
  model: string;
  mode: "local_only" | "cloud_only" | "automatic";
  fallbackReason?: string | null;
};

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
  /**
   * 原始错误码（如 `LOCAL_MODEL_NOT_FOUND`）。
   *
   * 🔴 不能从 `error`（已被翻译成中文）里反推：ChatPanel 需要按码决定
   * 是否显示「打开设置并选择模型」这类**可操作**的按钮，而中文文案会随
   * 翻译表变化，按字符串匹配等于把 UI 行为绑死在文案上。
   */
  errorCode: string | null;
  subscription: StreamSubscription | null;
  searchSuggestion: { userMessageId: string } | null;
  continuationUserMessageId: string | null;
  streamMode: "chat" | "search";
  warning: string | null;
  route: RouteView | null;
  start: (input: {
    requestId: string;
    sessionId: string;
    userMessage: string;
    continuationUserMessageId?: string;
  }) => void;
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
  errorCode: null,
  subscription: null,
  searchSuggestion: null,
  continuationUserMessageId: null,
  streamMode: "chat" as const,
  warning: null,
  route: null,
};

function mergeCitations(current: Citation[], incoming: Citation[]) {
  const identity = (citation: Citation) =>
    `${citation.sourceId}:${citation.kind === "memory" ? citation.memoryId : citation.kind === "web" ? citation.resultId : citation.chunkId}`;
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

/** 从错误事件负载里取原始错误码，供 UI 做「可操作」分支判定。 */
function errorCode(payload: Record<string, unknown>): string | null {
  const error =
    payload.error && typeof payload.error === "object"
      ? (payload.error as Record<string, unknown>)
      : payload;
  return typeof error.code === "string" && error.code ? error.code : null;
}

function errorMessage(payload: Record<string, unknown>) {
  // 直接复用 clientErrorMessage —— 它已覆盖 MODEL_TIMEOUT / MODEL_UNAVAILABLE /
  // MODEL_AUTH_FAILED / RATE_LIMITED / BACKEND_UNAVAILABLE 等全部模型错误码。
  // 此前这里另有一份同文案的小表，两边漂移时用户会看到不一致的提示。
  return clientErrorMessage({ code: errorCode(payload) ?? "" });
}

function parseRoute(payload: Record<string, unknown>): RouteView | null {
  const value = payload.route;
  if (!value || typeof value !== "object") return null;
  const route = value as Record<string, unknown>;
  if (
    (route.source !== "local" && route.source !== "cloud") ||
    typeof route.model !== "string" ||
    !route.model ||
    !["local_only", "cloud_only", "automatic"].includes(String(route.mode))
  )
    return null;
  return {
    source: route.source,
    model: route.model,
    mode: route.mode as RouteView["mode"],
    fallbackReason: typeof route.fallbackReason === "string" ? route.fallbackReason : null,
  };
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
      errorCode: null,
      subscription: null,
      searchSuggestion: null,
      continuationUserMessageId: continuationUserMessageId ?? null,
      streamMode: continuationUserMessageId ? "search" : "chat",
      warning: null,
      route: null,
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
    const eventRoute = parseRoute(event.payload);
    if (eventRoute) set({ route: eventRoute });
    if (event.type === "citations") {
      const parsed = CitationSchema.array().safeParse(event.payload.citations);
      set((state) => ({
        citations: parsed.success ? mergeCitations(state.citations, parsed.data) : state.citations,
        lastSequence: event.sequence,
      }));
      return true;
    }
    if (event.type === "done") {
      const suggested =
        event.payload.searchSuggested === true && typeof event.payload.userMessageId === "string"
          ? { userMessageId: event.payload.userMessageId }
          : null;
      const rawWarning = event.payload.warning;
      const fallbackReason =
        typeof event.payload.fallbackReason === "string" ? event.payload.fallbackReason : null;
      const warning =
        typeof rawWarning === "string"
          ? rawWarning
          : rawWarning &&
              typeof rawWarning === "object" &&
              typeof (rawWarning as Record<string, unknown>).message === "string"
            ? (rawWarning as Record<string, string>).message
            : null;
      set({
        ...initialState,
        sessionId: current.sessionId,
        userMessage: suggested ? current.userMessage : "",
        searchSuggestion: suggested,
        warning: warning ?? (fallbackReason ? `已自动切换到云端（${fallbackReason}）` : null),
        route: eventRoute ?? current.route,
        lastSequence: event.sequence,
      });
      return true;
    }
    if (event.type === "error") {
      set((state) => ({
        status: "error",
        error: errorMessage(event.payload),
        errorCode: errorCode(event.payload),
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
    set({
      requestId: null,
      subscription: null,
      status: "stopped",
      error: "已停止生成",
      errorCode: null,
    });
  },
  cancelForSession: (sessionId) => {
    const current = get();
    if (!sessionId || current.sessionId !== sessionId || current.status !== "streaming") return;
    current.subscription?.cancel();
    set(initialState);
  },
  reset: () => set(initialState),
}));
