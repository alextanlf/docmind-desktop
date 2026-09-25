import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { WebSearchSettings } from "../../renderer/src/features/settings/WebSearchSettings";
import { installDocMindApi, readySettings } from "./test-docmind-api";
describe("search settings", () => {
  const renderSettings = (settings = readySettings) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <WebSearchSettings settings={settings} />
      </QueryClientProvider>,
    );

  it("renders three explicit policy modes without the saved key", () => {
    installDocMindApi();
    renderSettings();
    expect(screen.getByRole("radio", { name: "关闭" })).toBeDefined();
    expect(screen.getByRole("radio", { name: "询问" })).toBeDefined();
    expect(screen.getByRole("radio", { name: "自动" })).toBeDefined();
    expect(screen.queryByDisplayValue("secret")).toBeNull();
  });

  it("shows the search source priority and their availability", () => {
    installDocMindApi();
    renderSettings({
      ...readySettings,
      webSearch: {
        ...readySettings.webSearch,
        hasApiKey: true,
        modelSearchAvailable: true,
        modelSearchLabel: "可用 · 模型内置联网（qwen-plus）",
      },
    });
    expect(screen.getByText("模型内置联网")).toBeDefined();
    expect(screen.getByText("可用 · 模型内置联网（qwen-plus）")).toBeDefined();
    expect(screen.getByText("已配置，作为第二来源")).toBeDefined();
    expect(screen.getByText("Bing / DuckDuckGo（无需配置，自动抓正文）")).toBeDefined();
  });

  it("shows the SearXNG advanced option and saves the instance", async () => {
    const api = installDocMindApi();
    renderSettings();
    await userEvent.click(screen.getByText("高级：自建 SearXNG / Tavily API Key"));
    await userEvent.type(
      screen.getByRole("textbox", { name: "SearXNG 实例地址" }),
      "https://searx.example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存联网设置" }));
    expect(api.settings.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({ searxngUrl: "https://searx.example.com" }),
    );
  });

  it("exposes the query rewrite switch", () => {
    installDocMindApi();
    renderSettings();
    const toggle = screen.getByRole("checkbox", {
      name: "智能改写搜索词（用当前模型生成 2-3 组查询，提升召回）",
    });
    expect(toggle).toBeChecked();
  });

  it("turns the query rewrite switch off", async () => {
    const api = installDocMindApi();
    renderSettings();
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "智能改写搜索词（用当前模型生成 2-3 组查询，提升召回）",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存联网设置" }));
    expect(api.settings.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({ queryRewrite: false }),
    );
  });

  it("marks unsupported model endpoints as skipped", () => {
    installDocMindApi();
    renderSettings();
    expect(screen.getByText("当前模型不支持内置联网，自动跳过")).toBeDefined();
    expect(screen.getAllByText("未配置，自动跳过")).toHaveLength(2);
  });
});
