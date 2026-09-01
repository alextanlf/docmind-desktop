import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { SettingsView } from "../../renderer/src/features/settings/SettingsView";
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

  it("retries a retryable settings query once but never retries auth errors", async () => {
    const retryableGet = vi
      .fn()
      .mockRejectedValueOnce({ code: "NETWORK_ERROR", retryable: true })
      .mockResolvedValue(readySettings);
    installDocMindApi({ settings: { get: retryableGet } });
    renderSettings();
    expect(await screen.findByText(readySettings.dataPath)).toBeVisible();
    expect(retryableGet).toHaveBeenCalledTimes(2);

    appQueryClient.clear();
    const authGet = vi.fn().mockRejectedValue({ code: "MODEL_AUTH_FAILED", retryable: true });
    installDocMindApi({ settings: { get: authGet } });
    renderSettings();
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(authGet).toHaveBeenCalledTimes(1);
  });
});
