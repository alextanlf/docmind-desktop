type ClientError = { code?: string; retryable?: boolean };

const messages: Record<string, string> = {
  BACKEND_PROTOCOL_ERROR: "应用与本地服务的数据格式不一致",
  APP_RESTART_UNAVAILABLE: "无法自动重启，请手动退出后重新打开应用",
  OLLAMA_UNAVAILABLE: "Ollama 未运行或暂时无法连接",
  OLLAMA_MODEL_NOT_INSTALLED: "选定模型尚未安装",
  OLLAMA_PULL_FAILED: "模型拉取失败",
  OLLAMA_PULL_CANCELLED: "模型拉取已取消",
  OLLAMA_PULL_INTERRUPTED: "应用退出时中断了模型拉取",
  OLLAMA_PROTOCOL_ERROR: "本地模型服务返回了无法识别的数据",
  LOCAL_MODEL_UNAVAILABLE: "本地模型暂时不可用",
  ROUTING_CLOUD_UNAVAILABLE: "本地和云端都不可用",
  YUQUE_BROWSER_UNAVAILABLE: "本机尚未安装语雀登录浏览器，暂无法打开登录窗口",
  YUQUE_PAGE_UNAVAILABLE: "语雀页面加载超时，请检查网络后重试",
  YUQUE_API_AUTH_FAILED: "语雀 API Token 无效或已过期",
  YUQUE_API_UNAVAILABLE: "无法连接语雀 API，请检查网络后重试",
  YUQUE_API_TOKEN_REQUIRED: "请先填写并保存语雀 API Token",
  REMOTE_LOGIN_REQUIRED: "远程知识库登录已失效，请重新登录",
  REMOTE_OPERATION_FAILED: "远程知识库操作失败，请稍后重试",
  REMOTE_NOT_BOUND: "知识库未绑定远程来源",
  REMOTE_PROVIDER_UNKNOWN: "未找到指定的远程知识库来源",
  REMOTE_CAPABILITY_UNSUPPORTED: "该远程来源不支持此操作",
  REMOTE_DISCOVERY_FAILED: "无法发现远程文档，请检查网络后重试",
  REMOTE_NOT_FOUND: "远程知识库或文档不存在或已不可用",
  FEISHU_AUTH_FAILED: "飞书 Webhook 无效或已失效",
  FEISHU_UNAVAILABLE: "无法连接飞书，请检查网络后重试",
  FEISHU_WEBHOOK_INVALID: "飞书 Webhook 地址格式无效",
  FEISHU_WEBHOOK_REQUIRED: "请先填写并保存飞书 Webhook",
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
  YUQUE_BROWSER_UNAVAILABLE: "安装后重试",
  YUQUE_PAGE_UNAVAILABLE: "检查网络后重试",
  YUQUE_API_AUTH_FAILED: "更新语雀 API Token",
  YUQUE_API_UNAVAILABLE: "检查网络后重试",
  REMOTE_LOGIN_REQUIRED: "重新登录",
  YUQUE_LOGIN_REQUIRED: "重新登录语雀",
  FEISHU_LOGIN_REQUIRED: "重新配置飞书凭证",
  REMOTE_OPERATION_FAILED: "重试远程操作",
  REMOTE_NOT_BOUND: "绑定远程来源",
  REMOTE_PROVIDER_UNKNOWN: "检查远程来源设置",
  REMOTE_CAPABILITY_UNSUPPORTED: "更换远程来源",
  REMOTE_DISCOVERY_FAILED: "检查网络后重试",
  REMOTE_NOT_FOUND: "重新选择远程来源",
  FEISHU_AUTH_FAILED: "更新飞书 Webhook",
  FEISHU_UNAVAILABLE: "检查网络后重试",
  FEISHU_WEBHOOK_INVALID: "检查 Webhook 地址",
};

export function isRetryable(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  return (error as ClientError).retryable === true;
}

export function errorAction(code: string | null | undefined): string {
  return (code && actions[code]) || "重试";
}

/**
 * Codes kept for compatibility with older backend builds. Lives at module level
 * with `messages`/`actions` so `clientErrorMessage` does not rebuild these 18
 * entries on every call.
 */
const legacyMessages: Record<string, string> = {
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
  YUQUE_BROWSER_UNAVAILABLE: "本机尚未安装语雀登录浏览器，暂无法打开登录窗口",
  EMBEDDING_DOWNLOAD_FAILED: "Embedding 模型下载失败，请检查网络后重试",
  YUQUE_LOGIN_REQUIRED: "语雀登录已失效，请重新登录",
  FEISHU_LOGIN_REQUIRED: "飞书访问凭证已失效，请重新配置或授权",
  BATCH_STALE_CONFIRMATION: "目录内容已变化，请重新选择目录后再试",
  BATCH_SOURCE_CHANGED: "目录内容已变化，请重新选择目录",
  BATCH_STATE_CONFLICT: "批量导入状态已变化，请刷新后重试",
};

const VALIDATION_CODES = ["VALIDATION_ERROR", "INVALID_REQUEST", "MODEL_VALIDATION_FAILED"];

export function clientErrorMessage(error: unknown): string {
  const value = error as ClientError | null;
  if (value?.code && messages[value.code]) return messages[value.code];
  if (value?.code && legacyMessages[value.code]) return legacyMessages[value.code];
  if (value?.code && VALIDATION_CODES.includes(value.code)) {
    return "设置内容无效，请检查填写内容";
  }
  return "操作失败，请检查设置后重试";
}
