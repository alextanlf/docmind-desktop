import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../renderer/src/App";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { installDocMindApi, loggedOutRemote, readySettings } from "./test-docmind-api";

/**
 * A snapshot with no API key anywhere.
 *
 * `hasApiKey` and `apiKeys` must agree: the form reads the per-preset map (a
 * DeepSeek key does not open Kimi), so a fixture that only clears the global
 * flag would still read as "key saved" for the selected preset.
 */
const noKeySettings = { ...readySettings, hasApiKey: false, apiKeys: {} };

describe("首次设置", () => {
  beforeEach(() => appQueryClient.clear());

  it("shows only the model step and does not check Yuque at startup", async () => {
    const api = installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });

    render(<App />);

    const dialog = await screen.findByRole("dialog", { name: "开始使用 DocMind" });
    const steps = within(dialog).getAllByRole("listitem");
    expect(steps).toHaveLength(1);
    expect(steps[0]).toHaveTextContent("1配置模型");
    expect(screen.getByRole("button", { name: "测试模型连接" })).toBeDisabled();
    expect(api.remote.status).not.toHaveBeenCalled();
  });

  it("enters the workspace after the model test without requiring Yuque", async () => {
    const api = installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
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
    fireEvent.click(screen.getByRole("button", { name: "进入工作台" }));
    expect(await screen.findByLabelText("工作台")).toBeVisible();
    expect(api.remote.login).not.toHaveBeenCalled();
  });

  it("invalidates a successful model test after edits, saves, or key clearing", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    render(<App />);

    const next = await screen.findByRole("button", { name: "进入工作台" });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-private-value" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await screen.findByText("连接成功，延迟 86 毫秒");
    await waitFor(() => expect(next).toBeEnabled());

    const testConnection = screen.getByRole("button", { name: "测试模型连接" });

    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "changed-model" } });
    expect(next).toBeDisabled();
    expect(testConnection).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await screen.findByText("连接成功，延迟 86 毫秒");
    expect(next).toBeEnabled();
    expect(testConnection).toBeEnabled();

    fireEvent.click(testConnection);
    await waitFor(() => expect(next).toBeEnabled());
    fireEvent.click(screen.getByRole("checkbox", { name: "清除已保存的 API Key" }));
    expect(next).toBeDisabled();
    expect(testConnection).toBeDisabled();
  });

  it("keeps onboarding focus inside the required modal", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    render(<App />);

    const dialog = await screen.findByRole("dialog", { name: "开始使用 DocMind" });
    const first = screen.getByRole("combobox", { name: "模型预设" });
    // The last focusable control is now the skip action in the footer.
    const last = screen.getByRole("button", { name: "跳过" });
    await waitFor(() => expect(first).toHaveFocus());

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(dialog).toBeVisible();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    const modalLayer = dialog.closest(".dialog-backdrop");
    const background = Array.from(document.body.children).find((element) => element !== modalLayer);
    expect(background).toHaveAttribute("inert");
    expect(background).toHaveAttribute("aria-hidden", "true");
  });

  it("lets the user skip model setup after confirming the consequence", async () => {
    const api = installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "跳过" }));

    // The consequence must be spelled out before the user is let in.
    const confirm = await screen.findByRole("dialog", { name: "跳过模型配置？" });
    expect(confirm).toHaveTextContent("问答与摘要功能不可用");
    expect(api.settings.skipModelSetup).not.toHaveBeenCalled();

    fireEvent.click(within(confirm).getByRole("button", { name: "跳过并进入" }));

    expect(await screen.findByLabelText("工作台")).toBeVisible();
    // The dismissal is persisted so the dialog stays gone on the next launch.
    await waitFor(() => expect(api.settings.skipModelSetup).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog", { name: "开始使用 DocMind" })).not.toBeInTheDocument();
  });

  it("returns to the form when cancelling the skip confirmation", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
      },
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "跳过" }));
    const confirm = await screen.findByRole("dialog", { name: "跳过模型配置？" });
    fireEvent.click(within(confirm).getByRole("button", { name: "返回配置" }));

    expect(await screen.findByRole("dialog", { name: "开始使用 DocMind" })).toBeVisible();
    expect(screen.getByLabelText("API Key")).toBeVisible();
  });

  it("does not show onboarding when a previous session skipped setup", async () => {
    const api = installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue({ ...noKeySettings, modelSetupSkipped: true }),
      },
    });

    render(<App />);

    expect(await screen.findByLabelText("工作台")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "开始使用 DocMind" })).not.toBeInTheDocument();
    expect(api.settings.skipModelSetup).not.toHaveBeenCalled();
  });

  it("still enters the workspace when persisting the skip fails", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockResolvedValue(noKeySettings),
        skipModelSetup: vi.fn().mockRejectedValue({ code: "BACKEND_UNAVAILABLE", retryable: false }),
      },
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "跳过" }));
    const confirm = await screen.findByRole("dialog", { name: "跳过模型配置？" });
    fireEvent.click(within(confirm).getByRole("button", { name: "跳过并进入" }));

    // A failed write must not trap the user in the dialog.
    expect(await screen.findByLabelText("工作台")).toBeVisible();
  });

  it("skips onboarding when the model key is ready, regardless of Yuque", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });

    render(<App />);

    expect(await screen.findByLabelText("工作台")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "开始使用 DocMind" })).not.toBeInTheDocument();
    expect(api.remote.status).not.toHaveBeenCalled();
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

  it("offers a restart when settings fail on a contract mismatch instead of a retry loop", async () => {
    const restart = vi.fn().mockResolvedValue(undefined);
    installDocMindApi({
      settings: {
        get: vi
          .fn()
          .mockRejectedValue({ code: "BACKEND_PROTOCOL_ERROR", message: "格式无效", retryable: true }),
      },
      app: { restart },
    });

    render(<App />);

    // A contract mismatch is deterministic: retrying re-runs the same failing
    // validation, so the dialog must not be retry-only or the user is trapped.
    const dialog = await screen.findByRole("dialog", {
      name: "本地服务返回了无法识别的数据",
    });
    expect(dialog).toHaveTextContent("版本不一致");

    const restartButton = within(dialog).getByRole("button", { name: "重启应用" });
    await fireEvent.click(restartButton);

    await waitFor(() => expect(restart).toHaveBeenCalledTimes(1));
  });

  it("isolates the real startup error modal and focuses its retry action", async () => {
    installDocMindApi({
      settings: {
        get: vi.fn().mockRejectedValue({ code: "BACKEND_UNAVAILABLE", retryable: false }),
      },
    });
    render(<App />);

    const dialog = await screen.findByRole("dialog", { name: "无法读取首次设置" });
    const retry = screen.getByRole("button", { name: "重新检查设置" });
    await waitFor(() => expect(retry).toHaveFocus());
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    const modalLayer = dialog.closest(".dialog-backdrop");
    const background = Array.from(document.body.children).find((element) => element !== modalLayer);
    expect(background).toHaveAttribute("inert");
    expect(background).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: "新建会话" })).not.toBeInTheDocument();
  });

  it("isolates workspace controls while initial settings are pending", async () => {
    let resolveSettings!: (value: typeof readySettings) => void;
    const pendingSettings = new Promise<typeof readySettings>((resolve) => {
      resolveSettings = resolve;
    });
    installDocMindApi({ settings: { get: vi.fn(() => pendingSettings) } });
    render(<App />);

    const dialog = await screen.findByRole("dialog", { name: "正在检查首次设置" });
    await waitFor(() => expect(dialog).toHaveFocus());
    const modalLayer = dialog.closest(".dialog-backdrop");
    const background = Array.from(document.body.children).find((element) => element !== modalLayer);
    expect(background).toHaveAttribute("inert");
    expect(background).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: "新建会话" })).not.toBeInTheDocument();

    await act(async () => resolveSettings(readySettings));
  });
});
