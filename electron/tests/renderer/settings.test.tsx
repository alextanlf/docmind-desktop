import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { SettingsView } from "../../renderer/src/features/settings/SettingsView";
import { clientErrorMessage } from "../../renderer/src/features/settings/settings.queries";
import {
  installDocMindApi,
  loggedOutYuque,
  readySettings,
  unavailableEmbedding,
} from "./test-docmind-api";

function renderSettings() {
  return render(
    <AppProviders>
      <SettingsView />
    </AppProviders>,
  );
}

describe("设置", () => {
  beforeEach(() => appQueryClient.clear());

  it("preserves a stored key when the password is blank and clears it only explicitly", async () => {
    const api = installDocMindApi();
    renderSettings();

    await screen.findByDisplayValue("deepseek-chat");
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    expect(api.settings.saveModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ apiKey: undefined }),
    );
    await screen.findByText("设置已保存");

    fireEvent.click(screen.getByRole("checkbox", { name: "清除已保存的 API Key" }));
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(api.settings.saveModel).toHaveBeenCalledTimes(2));
    expect(api.settings.saveModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ apiKey: "" }),
    );
  });

  it("maps model errors to an actionable Chinese message", async () => {
    installDocMindApi({
      settings: {
        testModel: vi.fn().mockRejectedValue({
          code: "MODEL_AUTH_FAILED",
          message: "unauthorized",
          retryable: false,
          action: "更新密钥",
        }),
      },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("API Key 无效，请更新密钥后重试");
  });

  it.each([
    ["MODEL_TIMEOUT", "模型连接超时，请检查网络或调大超时时间"],
    ["MODEL_RATE_LIMITED", "请求过于频繁，请稍后重试"],
    ["MODEL_PROTOCOL_ERROR", "模型服务响应格式异常，请检查 Base URL 或接口兼容性"],
    ["MODEL_UNAVAILABLE", "模型服务暂不可用，请稍后重试"],
  ])("maps %s without exposing backend details", (code, expected) => {
    expect(
      clientErrorMessage({
        code,
        message: "Invalid payload at /Users/private/config.json",
        action: "paste raw stack trace",
      }),
    ).toBe(expected);
  });

  it("uses generic Chinese copy for unknown and local validation errors", () => {
    const raw = "ZodError: invalid_type at /Users/private/config.json";
    expect(clientErrorMessage({ code: "UNKNOWN_INTERNAL", message: raw })).toBe(
      "操作失败，请检查设置后重试",
    );
    expect(clientErrorMessage({ code: "VALIDATION_ERROR", message: raw })).toBe(
      "设置内容无效，请检查填写内容",
    );
    expect(clientErrorMessage(new Error(raw))).not.toContain("ZodError");
    expect(clientErrorMessage(new Error(raw))).not.toContain("/Users/private");
  });

  it("shows embedding download details, progress, retry, and Yuque login controls", async () => {
    const api = installDocMindApi({
      yuque: { status: vi.fn().mockResolvedValue(loggedOutYuque) },
    });
    renderSettings();

    expect(await screen.findByText("约 400 MB，首次导入前需要下载")).toBeVisible();
    expect(screen.getByText("BAAI/bge-base-zh-v1.5")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));
    expect(await screen.findByRole("progressbar", { name: "Embedding 下载进度" })).toHaveAttribute(
      "aria-valuenow",
      "12",
    );
    fireEvent.click(screen.getByRole("button", { name: "登录语雀" }));
    expect(api.yuque.login).toHaveBeenCalledTimes(1);
  });

  it("offers retry after an embedding failure", async () => {
    installDocMindApi({
      embedding: {
        status: vi.fn().mockResolvedValue({
          ...unavailableEmbedding,
          state: "error",
          message: "下载失败，请检查网络",
        }),
      },
    });
    renderSettings();

    expect(await screen.findByRole("button", { name: "重试下载" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("下载失败，请检查网络");
  });

  it("confirms and clears only diagnostic screenshots, then refetches settings", async () => {
    const get = vi.fn().mockResolvedValue(readySettings);
    const api = installDocMindApi({ settings: { get } });
    renderSettings();

    expect(await screen.findByText(readySettings.dataPath)).toBeVisible();
    expect(screen.getByText("失败截图 2 张")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "清理失败截图" }));
    expect(screen.getByRole("dialog", { name: "确认清理失败截图" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认清理" }));

    await waitFor(() => expect(api.settings.clearDiagnostics).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/删除全部|清空数据/)).not.toBeInTheDocument();
  });

  it("traps confirmation focus, closes on Escape, and restores the trigger", async () => {
    installDocMindApi();
    renderSettings();

    const trigger = await screen.findByRole("button", { name: "清理失败截图" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "确认清理失败截图" });
    const close = screen.getByRole("button", { name: "关闭确认窗口" });
    await waitFor(() => expect(close).toHaveFocus());

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "确认清理失败截图" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("retries a retryable settings query once but excludes semantic non-retryable codes", async () => {
    const retryableGet = vi
      .fn()
      .mockRejectedValueOnce({ code: "NETWORK_ERROR", retryable: true })
      .mockResolvedValue(readySettings);
    installDocMindApi({ settings: { get: retryableGet } });
    renderSettings();
    expect(await screen.findByText(readySettings.dataPath)).toBeVisible();
    expect(retryableGet).toHaveBeenCalledTimes(2);

    for (const code of [
      "MODEL_AUTH_FAILED",
      "MODEL_PRESET_INVALID",
      "YUQUE_LOGIN_REQUIRED",
      "VALIDATION_ERROR",
      "DESTRUCTIVE_OPERATION",
    ]) {
      appQueryClient.clear();
      const get = vi.fn().mockRejectedValue({ code, retryable: true });
      installDocMindApi({ settings: { get } });
      const view = renderSettings();
      expect(await screen.findByRole("alert")).toBeVisible();
      expect(get).toHaveBeenCalledTimes(1);
      view.unmount();
    }
  });
});
