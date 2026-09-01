import { useQuery } from "@tanstack/react-query";

export const settingsKeys = {
  root: ["settings"] as const,
  embedding: ["embedding", "status"] as const,
  yuque: ["yuque", "status"] as const,
};

export function useSettingsQuery() {
  return useQuery({ queryKey: settingsKeys.root, queryFn: () => window.docmind.settings.get() });
}

export function useEmbeddingStatusQuery() {
  return useQuery({
    queryKey: settingsKeys.embedding,
    queryFn: () => window.docmind.embedding.status(),
    refetchInterval: (query) => (query.state.data?.state === "downloading" ? 1_000 : false),
  });
}

export function useYuqueStatusQuery() {
  return useQuery({ queryKey: settingsKeys.yuque, queryFn: () => window.docmind.yuque.status() });
}

export function clientErrorMessage(error: unknown): string {
  const value = error as { code?: string; message?: string; action?: string } | null;
  const messages: Record<string, string> = {
    MODEL_AUTH_FAILED: "API Key 无效，请更新密钥后重试",
    MODEL_NOT_FOUND: "未找到指定模型，请检查模型名称",
    MODEL_PRESET_INVALID: "模型预设无效，请重新选择",
    BACKEND_UNAVAILABLE: "本地服务暂不可用，请稍后重试",
    EMBEDDING_DOWNLOAD_FAILED: "Embedding 模型下载失败，请检查网络后重试",
    YUQUE_LOGIN_REQUIRED: "语雀登录已失效，请重新登录",
  };
  if (value?.code && messages[value.code]) return messages[value.code];
  if (value?.message) return value.action ? `${value.message}。${value.action}` : value.message;
  return "操作失败，请重试";
}
