type ClientError = { code?: string; retryable?: boolean };

const messages: Record<string, string> = {
  OLLAMA_UNAVAILABLE: "Ollama 未运行或暂时无法连接",
  OLLAMA_MODEL_NOT_INSTALLED: "选定模型尚未安装",
  OLLAMA_PULL_FAILED: "模型拉取失败",
  OLLAMA_PULL_CANCELLED: "模型拉取已取消",
  OLLAMA_PULL_INTERRUPTED: "应用退出时中断了模型拉取",
  OLLAMA_PROTOCOL_ERROR: "本地模型服务返回了无法识别的数据",
  LOCAL_MODEL_UNAVAILABLE: "本地模型暂时不可用",
  ROUTING_CLOUD_UNAVAILABLE: "本地和云端都不可用",
};

const actions: Record<string, string> = {
  OLLAMA_UNAVAILABLE: "重新检查 Ollama",
  OLLAMA_MODEL_NOT_INSTALLED: "打开设置并拉取模型",
  OLLAMA_PULL_FAILED: "重试拉取",
  OLLAMA_PULL_CANCELLED: "重新拉取",
  OLLAMA_PULL_INTERRUPTED: "重新拉取",
  OLLAMA_PROTOCOL_ERROR: "重新检查 Ollama",
  LOCAL_MODEL_UNAVAILABLE: "重试或切换模式",
  ROUTING_CLOUD_UNAVAILABLE: "检查设置并重试",
};

export function isRetryable(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  return (error as ClientError).retryable === true;
}

export function errorAction(code: string | null | undefined): string {
  return (code && actions[code]) || "重试";
}

export function clientErrorMessage(error: unknown): string {
  const value = error as ClientError | null;
  if (value?.code && messages[value.code]) return messages[value.code];
  const legacy: Record<string, string> = {
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
  if (value?.code && legacy[value.code]) return legacy[value.code];
  if (
    value?.code &&
    ["VALIDATION_ERROR", "INVALID_REQUEST", "MODEL_VALIDATION_FAILED"].includes(value.code)
  ) {
    return "设置内容无效，请检查填写内容";
  }
  return "操作失败，请检查设置后重试";
}
