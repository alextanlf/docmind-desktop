import { render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import type { SettingsView } from "../../shared/contracts";
import { RuntimeModelSettings } from "../../renderer/src/features/settings/RuntimeModelSettings";
import {
  clientErrorMessage,
  localModelKeys,
  settingsKeys,
  useLocalModelsQuery,
  useLocalModelStatusQuery,
  useSaveRuntimeMutation,
} from "../../renderer/src/features/settings/settings.queries";
import { installDocMindApi, readySettings } from "./test-docmind-api";

function Probe({ visible = true }: { visible?: boolean }) {
  useLocalModelStatusQuery(visible);
  useLocalModelsQuery(visible);
  return null;
}

describe("runtime query contracts", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("uses fixed keys and does not query the local server when hidden", async () => {
    const api = installDocMindApi();
    render(
      <QueryClientProvider client={appQueryClient}>
        <Probe visible={false} />
      </QueryClientProvider>,
    );
    await Promise.resolve();
    expect(api.localModel.status).not.toHaveBeenCalled();
    expect(api.localModel.models).not.toHaveBeenCalled();
    expect(localModelKeys.status).toEqual(["localModel", "status"]);
    // 原先这里还断言 settingsKeys.runtime === ["settings","runtime"]，那是纯字面量
    // 断言（不验证任何行为），而该 key 唯一的使用者 useRuntimeSettingsQuery 已零调用、
    // setQueryData 也已删除，故 key 一并移除。
  });

  it.each([
    ["LOCAL_MODEL_UNAVAILABLE", "本地模型服务未运行或暂时无法连接"],
    ["LOCAL_MODEL_NOT_FOUND", "该模型未在本地服务中加载，请检查模型名称"],
    ["LOCAL_MODEL_PROTOCOL_ERROR", "本地模型服务返回了无法识别的数据"],
    ["LOCAL_MODEL_AUTH_FAILED", "本地模型服务拒绝了该凭据"],
    ["LOCAL_MODEL_TIMEOUT", "本地模型服务响应超时"],
    ["ROUTING_CLOUD_UNAVAILABLE", "本地和云端都不可用"],
  ])("maps %s to safe copy", (code, expected) => {
    expect(clientErrorMessage({ code })).toBe(expected);
  });

  it("updates settings root only after a successful runtime save", async () => {
    const api = installDocMindApi({
      settings: { saveRuntime: vi.fn().mockRejectedValue({ code: "INVALID_REQUEST" }) },
    });
    appQueryClient.setQueryData(settingsKeys.root, readySettings);
    const result = renderHookHarness(() => useSaveRuntimeMutation());
    await expect(
      result.mutateAsync({
        local: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", apiKey: "", timeoutSeconds: 120 },
        routing: { mode: "local_only" },
        rag: { maxSources: 5, memoryRecallMinSimilarity: 0.65 },
      }),
    ).rejects.toBeDefined();
    await waitFor(() => expect(api.settings.saveRuntime).toHaveBeenCalled());
    expect(appQueryClient.getQueryData(settingsKeys.root)).toEqual(readySettings);
  });
});

function renderHookHarness<T>(factory: () => T): T {
  let value!: T;
  function Harness() {
    value = factory();
    return null;
  }
  render(
    <QueryClientProvider client={appQueryClient}>
      <Harness />
    </QueryClientProvider>,
  );
  return value;
}

describe("retrieval settings form", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  function renderForm() {
    const settings: SettingsView = {
      ...readySettings,
      runtime: {
        local: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", apiKey: "", timeoutSeconds: 120 },
        routing: { mode: "cloud_only" },
        rag: { maxSources: 5, memoryRecallMinSimilarity: 0.65 },
      },
    };
    return render(
      <QueryClientProvider client={appQueryClient}>
        <RuntimeModelSettings settings={settings} />
      </QueryClientProvider>,
    );
  }

  it("shows the persisted retrieval limits instead of the fallback", () => {
    const { getByLabelText } = renderForm();

    expect(getByLabelText(/片段上限/)).toHaveValue(5);
    expect(getByLabelText(/记忆召回相似度/)).toHaveValue(0.65);
  });

  it("explains that the similarity floor only gates memory recall", () => {
    const { container } = renderForm();

    expect(container.textContent).toContain("只影响记忆召回");
  });

  // A runtime persisted before the rag key existed must still render; the inputs
  // are controlled, so a missing field would throw instead of showing a default.
  it("falls back to defaults when the stored runtime predates the rag key", () => {
    const settings = {
      ...readySettings,
      runtime: {
        local: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", apiKey: "", timeoutSeconds: 120 },
        routing: { mode: "cloud_only" as const },
      },
    } as unknown as SettingsView;

    const { getByLabelText } = render(
      <QueryClientProvider client={appQueryClient}>
        <RuntimeModelSettings settings={settings} />
      </QueryClientProvider>,
    );

    expect(getByLabelText(/片段上限/)).toHaveValue(5);
    expect(getByLabelText(/记忆召回相似度/)).toHaveValue(0.65);
  });
  // 🔴 这条断言是本次改动的核心判据：模型必须是「从服务已加载列表里选」，
  // 而不是让用户手打字符串。此前后端已经把 /api/ollama/models 查出来渲染成
  // 只读列表，选择权却交还给了自由文本框。
  it("offers the server's loaded models as a dropdown rather than a free-text box", async () => {
    const api = installDocMindApi({
      localModel: {
        status: vi.fn().mockResolvedValue({
          available: true,
          baseUrl: "http://127.0.0.1:1234",
          version: null,
          selectedModel: "qwen2.5:0.5b",
          selectedModelAvailable: true,
          checkedAt: "2026-08-31T08:00:00Z",
          message: "本地模型服务已连接",
        }),
        models: vi.fn().mockResolvedValue({
          available: true,
          models: [
            { id: "qwen2.5:0.5b", label: "qwen2.5:0.5b" },
            { id: "gemma-3-4b", label: "gemma-3-4b" },
          ],
          checkedAt: "2026-08-31T08:00:00Z",
          message: "本地模型服务已连接",
        }),
      },
    });

    const settings = {
      ...readySettings,
      runtime: {
        local: {
          baseUrl: "http://127.0.0.1:1234",
          model: "qwen2.5:0.5b",
          apiKey: "",
          timeoutSeconds: 120,
        },
        routing: { mode: "local_only" as const },
      },
    } as unknown as SettingsView;

    const { getByLabelText } = render(
      <QueryClientProvider client={appQueryClient}>
        <RuntimeModelSettings settings={settings} />
      </QueryClientProvider>,
    );

    // Wait for the catalogue to land: the field starts as a text box and
    // becomes a select once the server's model list arrives.
    const select = await waitFor(() => {
      const field = getByLabelText("本地模型");
      expect(field.tagName).toBe("SELECT");
      return field;
    });
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(["qwen2.5:0.5b", "gemma-3-4b"]);
    expect(getByLabelText("本地模型")).toHaveValue("qwen2.5:0.5b");
    expect(api.localModel.models).toHaveBeenCalled();
  });

  it("falls back to a text box only when the server reports no models", async () => {
    installDocMindApi({
      localModel: {
        models: vi.fn().mockResolvedValue({
          available: true,
          models: [],
          checkedAt: "2026-08-31T08:00:00Z",
          message: "本地模型服务已连接",
        }),
      },
    });
    const { findByLabelText } = render(
      <QueryClientProvider client={appQueryClient}>
        <RuntimeModelSettings settings={readySettings as SettingsView} />
      </QueryClientProvider>,
    );
    const field = await findByLabelText("本地模型");
    expect(field.tagName).toBe("INPUT");
  });
});
