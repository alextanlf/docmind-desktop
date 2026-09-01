import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../renderer/src/App";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { installDocMindApi, loggedOutYuque, readySettings } from "./test-docmind-api";

describe("首次设置", () => {
  beforeEach(() => appQueryClient.clear());

  it("shows exactly two numbered steps until model and Yuque are ready", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue({ ...readySettings, hasApiKey: false }),
      },
      yuque: { status: vi.fn().mockResolvedValue(loggedOutYuque) },
    });

    render(<App />);

    const dialog = await screen.findByRole("dialog", { name: "开始使用 DocMind" });
    const steps = within(dialog).getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent("1配置模型");
    expect(steps[1]).toHaveTextContent("2登录语雀");
    expect(screen.getByRole("button", { name: "测试模型连接" })).toBeDisabled();
  });

  it("advances only after a saved key passes connection test and Yuque login succeeds", async () => {
    const api = installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue({ ...readySettings, hasApiKey: false }),
      },
      yuque: { status: vi.fn().mockResolvedValue(loggedOutYuque) },
    });
    render(<App />);

    const keyInput = await screen.findByLabelText("API Key");
    fireEvent.change(keyInput, { target: { value: "sk-private-value" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    expect(api.settings.saveModel).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "sk-private-value" }),
    );
    expect(appQueryClient.getQueryData(["api-key"])).toBeUndefined();

    const testConnection = screen.getByRole("button", { name: "测试模型连接" });
    await waitFor(() => expect(testConnection).toBeEnabled());
    fireEvent.click(testConnection);
    expect(await screen.findByText("连接成功，延迟 86 毫秒")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));

    expect(screen.getByRole("heading", { name: "登录语雀" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "打开语雀登录" }));
    await waitFor(() => expect(api.yuque.login).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "进入工作台" }));
    expect(await screen.findByLabelText("工作台")).toBeVisible();
  });

  it("skips onboarding when the model key and Yuque session are ready", async () => {
    installDocMindApi({
      yuque: {
        status: vi.fn().mockResolvedValue({
          loggedIn: true,
          accountLabel: "测试账号",
          requiresLogin: false,
        }),
      },
    });

    render(<App />);

    expect(await screen.findByLabelText("工作台")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "开始使用 DocMind" })).not.toBeInTheDocument();
  });

  it("keeps a recovery action visible when initial settings cannot be read", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockRejectedValue({
          code: "BACKEND_UNAVAILABLE",
          message: "本地服务不可用",
          retryable: false,
        }),
      },
    });

    render(<App />);

    expect(await screen.findByRole("dialog", { name: "无法读取首次设置" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重新检查设置" })).toBeVisible();
  });
});
