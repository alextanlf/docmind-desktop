import { render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import type { SettingsView } from "../../shared/contracts";
import { RuntimeModelSettings } from "../../renderer/src/features/settings/RuntimeModelSettings";
import {
  clientErrorMessage,
  ollamaKeys,
  settingsKeys,
  useOllamaModelsQuery,
  useOllamaStatusQuery,
  useSaveRuntimeMutation,
} from "../../renderer/src/features/settings/settings.queries";
import { installDocMindApi, readySettings } from "./test-docmind-api";

function Probe({ visible = true }: { visible?: boolean }) {
  useOllamaStatusQuery(visible);
  useOllamaModelsQuery(visible);
  return null;
}

describe("runtime query contracts", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("uses fixed keys and does not query Ollama when hidden", async () => {
    const api = installDocMindApi();
    render(
      <QueryClientProvider client={appQueryClient}>
        <Probe visible={false} />
      </QueryClientProvider>,
    );
    await Promise.resolve();
    expect(api.ollama.status).not.toHaveBeenCalled();
    expect(api.ollama.models).not.toHaveBeenCalled();
    expect(ollamaKeys.status).toEqual(["ollama", "status"]);
    expect(settingsKeys.runtime).toEqual(["settings", "runtime"]);
  });

  it.each([
    ["OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接"],
    ["OLLAMA_MODEL_NOT_INSTALLED", "选定模型尚未安装"],
    ["OLLAMA_PULL_FAILED", "模型拉取失败"],
    ["OLLAMA_PULL_CANCELLED", "模型拉取已取消"],
    ["OLLAMA_PULL_INTERRUPTED", "应用退出时中断了模型拉取"],
    ["OLLAMA_PROTOCOL_ERROR", "本地模型服务返回了无法识别的数据"],
    ["LOCAL_MODEL_UNAVAILABLE", "本地模型暂时不可用"],
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
        ollama: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", timeoutSeconds: 60 },
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
        ollama: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", timeoutSeconds: 60 },
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
        ollama: { baseUrl: "http://127.0.0.1:11434", model: "llama3.2", timeoutSeconds: 60 },
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
});
