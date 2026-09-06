import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { WebSearchSettingsInput } from "../../../../shared/contracts";

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

export function useSaveWebSearchMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: WebSearchSettingsInput) => window.docmind.settings.saveWebSearch(input),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}

export function clientErrorMessage(error: unknown): string {
  const value = error as { code?: string } | null;
  const messages: Record<string, string> = {
    MODEL_AUTH_FAILED: "API Key 无效，请更新密钥后重试",
    MODEL_NOT_FOUND: "未找到指定模型，请检查模型名称",
    MODEL_PRESET_INVALID: "模型预设无效，请重新选择",
    MODEL_TIMEOUT: "模型连接超时，请检查网络或调大超时时间",
    RATE_LIMITED: "请求过于频繁，请稍后重试",
    MODEL_RATE_LIMITED: "请求过于频繁，请稍后重试",
    PROTOCOL_ERROR: "模型服务响应格式异常，请检查 Base URL 或接口兼容性",
    MODEL_PROTOCOL_ERROR: "模型服务响应格式异常，请检查 Base URL 或接口兼容性",
    UNAVAILABLE: "模型服务暂不可用，请稍后重试",
    MODEL_UNAVAILABLE: "模型服务暂不可用，请稍后重试",
    BACKEND_UNAVAILABLE: "本地服务暂不可用，请稍后重试",
    EMBEDDING_DOWNLOAD_FAILED: "Embedding 模型下载失败，请检查网络后重试",
    YUQUE_LOGIN_REQUIRED: "语雀登录已失效，请重新登录",
    BATCH_STALE_CONFIRMATION: "目录内容已变化，请重新选择目录后再试",
    BATCH_SOURCE_CHANGED: "目录内容已变化，请重新选择目录",
    BATCH_STATE_CONFLICT: "批量导入状态已变化，请刷新后重试",
  };
  if (value?.code && messages[value.code]) return messages[value.code];
  if (
    value?.code &&
    ["VALIDATION_ERROR", "INVALID_REQUEST", "MODEL_VALIDATION_FAILED"].includes(value.code)
  )
    return "设置内容无效，请检查填写内容";
  return "操作失败，请检查设置后重试";
}
