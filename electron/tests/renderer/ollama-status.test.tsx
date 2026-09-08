import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RuntimeModelSettings } from "../../renderer/src/features/settings/RuntimeModelSettings";
import { OllamaStatusCard } from "../../renderer/src/features/settings/OllamaStatusCard";
import type { OllamaModelsView, OllamaStatusView, SettingsView } from "../../shared/contracts";
import { installDocMindApi, readySettings } from "./test-docmind-api";

const baseRuntime = {
  ollama: { baseUrl: "http://127.0.0.1:11434", model: "qwen2.5:7b", timeoutSeconds: 60 },
  routing: { mode: "cloud_only" as const },
};
const settings: SettingsView = { ...readySettings, runtime: baseRuntime };
const unavailable: OllamaStatusView = {
  available: false,
  baseUrl: baseRuntime.ollama.baseUrl,
  version: null,
  selectedModel: baseRuntime.ollama.model,
  selectedModelInstalled: false,
  checkedAt: "2026-09-07T00:00:00Z",
  message: "Ollama 未运行",
};
const connectedMissing: OllamaStatusView = {
  ...unavailable,
  available: true,
  version: "0.11.0",
  message: "已连接",
};
const emptyModels: OllamaModelsView = {
  available: true,
  models: [],
  checkedAt: unavailable.checkedAt,
  message: "没有已安装模型",
};

function renderWithQuery(ui: React.ReactNode) {
  return render(<QueryClientProvider client={appQueryClient}>{ui}</QueryClientProvider>);
}

describe("Ollama settings controls", () => {
  beforeEach(() => appQueryClient.clear());

  it("renders keyboard-selectable modes and local privacy copy", () => {
    installDocMindApi({
      ollama: {
        status: vi.fn().mockResolvedValue(unavailable),
        models: vi.fn().mockResolvedValue(emptyModels),
      },
    });
    renderWithQuery(<RuntimeModelSettings settings={settings} />);
    const group = screen.getByRole("radiogroup", { name: "对话运行方式" });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    fireEvent.click(screen.getByRole("radio", { name: /仅本地/ }));
    expect(screen.getByText("内容不会发送到云端")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /仅本地/ })).toHaveAttribute("aria-checked", "true");
    expect(group).toBeInTheDocument();
  });

  it("distinguishes Ollama unavailable from a missing selected model", () => {
    const { rerender } = renderWithQuery(
      <OllamaStatusCard status={unavailable} models={emptyModels} />,
    );
    expect(screen.getByText("Ollama 未运行")).toBeInTheDocument();
    rerender(
      <QueryClientProvider client={appQueryClient}>
        <OllamaStatusCard status={connectedMissing} models={emptyModels} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("模型尚未安装")).toBeInTheDocument();
  });
});
