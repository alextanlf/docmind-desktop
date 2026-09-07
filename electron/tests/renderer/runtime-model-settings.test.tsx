import { render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
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
