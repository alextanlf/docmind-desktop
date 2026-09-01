import type { ModelStatus, SettingsView, YuqueStatus } from "../../shared/contracts";
import { vi } from "vitest";

export const readySettings: SettingsView = {
  model: {
    preset: "deepseek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    timeoutSeconds: 30,
  },
  hasApiKey: true,
  dataPath: "/Users/test/Library/Application Support/DocMind",
  screenshotCount: 2,
};

export const unavailableEmbedding: ModelStatus = {
  state: "unavailable",
  modelName: "BAAI/bge-base-zh-v1.5",
  dimension: null,
  message: "尚未下载",
  progress: 0,
};

export const loggedOutYuque: YuqueStatus = {
  loggedIn: false,
  accountLabel: null,
  requiresLogin: true,
};

export function installDocMindApi(overrides?: {
  settings?: Partial<typeof window.docmind.settings>;
  embedding?: Partial<typeof window.docmind.embedding>;
  yuque?: Partial<typeof window.docmind.yuque>;
}) {
  const api = {
    settings: {
      get: vi.fn().mockResolvedValue(readySettings),
      saveModel: vi.fn().mockResolvedValue(readySettings),
      testModel: vi.fn().mockResolvedValue({ connected: true, latencyMs: 86 }),
      clearDiagnostics: vi.fn().mockResolvedValue(undefined),
      ...overrides?.settings,
    },
    embedding: {
      status: vi.fn().mockResolvedValue(unavailableEmbedding),
      prepare: vi.fn().mockResolvedValue({
        ...unavailableEmbedding,
        state: "downloading",
        progress: 12,
        message: "正在下载",
      }),
      ...overrides?.embedding,
    },
    yuque: {
      status: vi.fn().mockResolvedValue(loggedOutYuque),
      login: vi.fn().mockResolvedValue({
        loggedIn: true,
        accountLabel: "DocMind 测试账号",
        requiresLogin: false,
      }),
      ...overrides?.yuque,
    },
  };

  Object.defineProperty(window, "docmind", {
    configurable: true,
    value: api,
  });
  return api;
}
