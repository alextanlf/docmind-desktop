import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { SettingsView } from "../../renderer/src/features/settings/SettingsView";
import { clientErrorMessage } from "../../renderer/src/features/settings/settings.queries";
import {
  installDocMindApi,
  loggedOutRemote,
  readySettings,
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

    await screen.findByRole("option", { name: /deepseek-flash/ });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    expect(api.settings.saveModel).toHaveBeenLastCalledWith(
      expect.objectContaining({ apiKey: undefined }),
    );
    await screen.findByText("连接成功，延迟 86 毫秒");

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

  it("jumps between settings sections from the section navigation", async () => {
    installDocMindApi();
    renderSettings();

    await screen.findByRole("option", { name: /deepseek-flash/ });
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    expect(within(nav).getAllByRole("button")).toHaveLength(5);

    const target = within(nav).getByRole("button", { name: "本地数据与诊断" });
    fireEvent.click(target);

    expect(target).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("heading", { name: "本地数据与诊断" }).closest("section"),
    ).toHaveFocus();
  });

  it("shows remote login controls without any embedding panel", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    renderSettings();

    // 嵌入模型随应用分发且启动即自动预热，设置页不再暴露它的状态/加载入口。
    expect(screen.queryByText("Embedding 模型")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载模型" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新加载" })).not.toBeInTheDocument();
    expect(api.embedding.prepare).not.toHaveBeenCalled();
    expect(screen.queryByRole("progressbar", { name: "Embedding 加载进度" })).not.toBeInTheDocument();

    expect(await screen.findByRole("button", { name: "登录语雀" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "登录语雀" }));
    expect(api.remote.login).toHaveBeenCalledWith("yuque");
  });

  it("shows one card per provider, each rendering its own credential channels", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    renderSettings();

    // One card per registered provider (from remote.listProviders). Every
    // channel a provider declares — including the Feishu bot webhook and the
    // Yuque API token — is rendered by the generic, data-driven path.
    expect(await screen.findByRole("heading", { name: "语雀" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "飞书文档" })).toBeVisible();
    // The hard-coded Feishu card is gone: the webhook is a channel on the
    // Feishu provider card now, so no second binding card may appear.
    expect(screen.queryByRole("heading", { name: "飞书绑定" })).not.toBeInTheDocument();
    expect(api.remote.listProviders).toHaveBeenCalledTimes(1);
    expect(api.remote.listCredentials).toHaveBeenCalledWith("yuque");
    expect(api.remote.listCredentials).toHaveBeenCalledWith("feishu");
    // Wait for the credential channel forms before counting badges.
    expect(await screen.findByLabelText("语雀 API")).toBeVisible();
    expect(await screen.findByLabelText("飞书自建应用")).toBeVisible();
    expect(await screen.findByLabelText("飞书机器人")).toBeVisible();
    // 语雀 API、飞书自建应用、飞书机器人（webhook）三张 secret 表单初始均为未绑定。
    expect(screen.getAllByText("未绑定")).toHaveLength(3);
    // 飞书 user 通道是 OAuth 授权入口，无安装浏览器按钮。
    expect(screen.getByRole("button", { name: "登录飞书文档" })).toBeVisible();

    // 飞书自建应用通道（第二张“保存并验证”按钮属于飞书卡片）。
    fireEvent.change(screen.getByLabelText("飞书自建应用"), {
      target: { value: "cli_a1b2:s3cret" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "保存并验证" })[1]);
    await screen.findByText("语雀 API 已连接");
    expect(api.remote.saveCredential).toHaveBeenCalledWith("feishu", "app", {
      secret: "cli_a1b2:s3cret",
    });
    expect(api.remote.testCredential).toHaveBeenCalledWith("feishu", "app");

    fireEvent.change(screen.getByLabelText("语雀 API"), {
      target: { value: "yuque-token" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "保存并验证" })[0]);
    await waitFor(() =>
      expect(api.remote.saveCredential).toHaveBeenCalledWith("yuque", "api", {
        secret: "yuque-token",
      }),
    );
    expect(api.remote.testCredential).toHaveBeenCalledWith("yuque", "api");
  });

  it("derives the connections summary from the registered providers", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();

    // Regression: the description used to be a static sentence in SettingsView
    // that named vendors, so it went stale the moment a provider was added or
    // removed. It must now be built from the registry's own labels.
    expect(
      await screen.findByText("语雀、飞书文档均为可选连接，不影响 DocMind 启动"),
    ).toBeVisible();
  });

  it("summary falls back to neutral copy when no provider is registered", async () => {
    installDocMindApi({
      remote: {
        status: vi.fn().mockResolvedValue(loggedOutRemote),
        listProviders: vi.fn().mockResolvedValue([]),
      },
    });
    renderSettings();

    expect(
      await screen.findByText("远程来源均为可选连接，不影响 DocMind 启动"),
    ).toBeVisible();
  });

  it("renders channel-declared hints instead of inventing provider copy", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();

    // A URL-shaped credential must not be labelled "… Token", and the form
    // has to surface the provider's own placeholder and help link.
    const webhookInput = await screen.findByLabelText("飞书机器人");
    expect(webhookInput).toHaveAttribute(
      "placeholder",
      "https://open.feishu.cn/open-apis/bot/v2/hook/…",
    );
    // The webhook channel supplies the help link itself; the copy comes from
    // the channel descriptor rather than any hard-coded vendor string.
    expect(screen.getAllByRole("link", { name: /添加机器人/ })).toHaveLength(1);
    // Channels supply their own placeholder too, instead of the generic copy.
    expect(await screen.findByLabelText("语雀 API")).toHaveAttribute(
      "placeholder",
      "粘贴语雀个人访问令牌",
    );
  });

  it("falls back to generic copy when a channel declares no hint", async () => {
    installDocMindApi({
      remote: {
        status: vi.fn().mockResolvedValue(loggedOutRemote),
        listCredentials: vi.fn().mockResolvedValue([
          {
            provider: "acme",
            channel: "api",
            label: "Acme API",
            configured: false,
            state: "disconnected",
            accountLabel: null,
            hasSecret: true,
          },
        ]),
      },
    });
    renderSettings();

    expect(await screen.findByLabelText("Acme API")).toHaveAttribute(
      "placeholder",
      "输入访问凭据",
    );
    expect(screen.queryByRole("link", { name: /获取凭据/ })).toBeNull();
  });

  it("installs the Yuque browser when login reports a missing browser", async () => {
    const api = installDocMindApi({
      remote: {
        status: vi.fn().mockResolvedValue(loggedOutRemote),
        login: vi.fn().mockRejectedValue({
          code: "YUQUE_BROWSER_UNAVAILABLE",
          message: "本机尚未安装语雀登录浏览器",
          retryable: true,
        }),
      },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "登录语雀" }));
    fireEvent.click(await screen.findByRole("button", { name: "安装浏览器" }));

    expect(await screen.findByText("语雀浏览器已安装")).toBeVisible();
    expect(api.remote.installBrowser).toHaveBeenCalledWith("yuque");
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

  it("saves the chosen reasoning level for a vendor that supports it", async () => {
    const api = installDocMindApi();
    renderSettings();

    const effort = await screen.findByLabelText("推理强度");
    // The saved level from settings is preselected.
    expect(effort).toHaveValue("high");
    expect(within(effort as HTMLSelectElement).getByRole("option", { name: "关闭思考（off）" })).toBeVisible();

    fireEvent.change(effort, { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ reasoningEffort: "low" }),
      ),
    );
  });

  it("offers only the levels the selected model accepts, not the vendor's", async () => {
    const api = installDocMindApi();
    renderSettings();

    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "kimi" } });
    const effort = await screen.findByLabelText("推理强度");
    // Kimi K3 always reasons, so no "off" even though DeepSeek (same "thinking"
    // concept) allows it. Its levels are the vendor's own (low/high/max), and
    // the documented default is max.
    expect(within(effort as HTMLSelectElement).queryByRole("option", { name: "关闭思考（off）" })).toBeNull();
    expect(within(effort as HTMLSelectElement).getByRole("option", { name: "最高（max）" })).toBeVisible();
    expect(effort).toHaveValue("max");

    // Switching to K2.6 changes the allowed levels for the same vendor: it is a
    // plain on/off toggle, not an intensity scale.
    fireEvent.change(await screen.findByLabelText("模型名称"), { target: { value: "kimi-k2.6" } });
    const toggled = screen.getByLabelText("推理强度") as HTMLSelectElement;
    expect(within(toggled).getByRole("option", { name: "关闭思考（off）" })).toBeVisible();
    expect(within(toggled).getByRole("option", { name: "开启思考（on）" })).toBeVisible();
    expect(within(toggled).queryByRole("option", { name: "最高（max）" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ model: "kimi-k2.6" }),
      ),
    );
  });

  it("drops the off level for a model that forces thinking on", async () => {
    installDocMindApi();
    renderSettings();

    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "glm" } });
    // GLM-4.6 can be switched off, GLM-5.3 cannot.
    expect(
      within(screen.getByLabelText("推理强度") as HTMLSelectElement).getByRole("option", {
        name: "关闭思考（off）",
      }),
    ).toBeVisible();

    fireEvent.change(await screen.findByLabelText("模型名称"), { target: { value: "glm-5.3" } });
    expect(
      within(screen.getByLabelText("推理强度") as HTMLSelectElement).queryByRole("option", {
        name: "关闭思考（off）",
      }),
    ).toBeNull();
  });

  it("explains the absence of a level picker for gateways without one", async () => {
    const api = installDocMindApi();
    renderSettings();

    fireEvent.change(await screen.findByLabelText("模型预设"), {
      target: { value: "opencode_zen" },
    });

    expect(await screen.findByText("该服务商未提供推理档位，使用其默认行为")).toBeVisible();
    expect(screen.queryByLabelText("推理强度")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ preset: "opencode_zen", baseUrl: "https://opencode.ai/zen/v1" }),
      ),
    );
  });

  it("offers curated model choices per vendor and saves the picked one", async () => {
    const api = installDocMindApi();
    renderSettings();

    const preset = await screen.findByLabelText("模型预设");
    fireEvent.change(preset, { target: { value: "kimi" } });
    const picker = await screen.findByLabelText("模型名称");
    expect(picker).toHaveValue("kimi-k3");

    fireEvent.change(picker, { target: { value: "kimi-k2.6" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ preset: "kimi", model: "kimi-k2.6" }),
      ),
    );
  });

  it("keeps a hand-typed model id even after the vendor list loads", async () => {
    const api = installDocMindApi({
      settings: {
        listModels: vi.fn().mockResolvedValue({
          models: [
            { id: "kimi-k2.5", label: "Kimi K2.5" },
            { id: "kimi-k2", label: "Kimi K2" },
          ],
        }),
      },
    });
    renderSettings();

    // Choosing "自定义…" must reveal a text field, and the typed id must
    // survive saving even though the provider never reported it.
    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "kimi" } });
    // Pick "自定义…" from the dropdown, which swaps in a text field.
    fireEvent.change(await screen.findByLabelText("模型名称"), {
      target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("模型名称"), {
      target: { value: "kimi-internal-build" } });
    fireEvent.click(screen.getByRole("button", { name: "获取模型列表" }));
    await screen.findByText("已获取 2 个可用模型");

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ model: "kimi-internal-build" }),
      ),
    );
  });

  it("falls back to a free-text model field when the vendor has no catalogue", async () => {
    const api = installDocMindApi();
    renderSettings();

    // The custom preset ships no curated models, so the picker degrades to free
    // text and an arbitrary model id stays saveable.
    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "custom" } });
    const field = await screen.findByLabelText("模型名称");
    expect(field.tagName).toBe("INPUT");
    fireEvent.change(field, { target: { value: "my-own-model" } });

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ preset: "custom", model: "my-own-model" }),
      ),
    );
  });

  it("refreshes the picker from the provider's live model list", async () => {
    const listModels = vi.fn().mockResolvedValue({
      models: [
        { id: "kimi-k3", label: "Kimi K3" },
        { id: "kimi-k2.5", label: "Kimi K2.5" },
      ],
      source: "live",
      notice: null,
    });
    const api = installDocMindApi({ settings: { listModels } });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "获取模型列表" }));

    expect(await screen.findByText("已获取 2 个可用模型")).toBeVisible();
    expect(listModels).toHaveBeenCalledWith(
      expect.objectContaining({ baseUrl: "https://api.deepseek.com" }),
    );
    // The freshly fetched model becomes selectable and can be saved.
    const picker = screen.getByLabelText("模型名称");
    fireEvent.change(picker, { target: { value: "kimi-k3" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ model: "kimi-k3" }),
      ),
    );
  });

  it("reports a provider that returns no models instead of silently showing none", async () => {
    installDocMindApi({ settings: { listModels: vi.fn().mockResolvedValue({ models: [], source: "live", notice: null }) } });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "获取模型列表" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该服务商未返回模型列表");
  });

  // 回归：后端早期没有从表单接收 preset，只能退回 saved.preset，于是无论选哪家
  // 都返回空列表。这里锁住「预设必须随请求一起发出去」。
  it("sends the selected preset when refreshing the model list", async () => {
    const listModels = vi.fn().mockResolvedValue({
      models: [{ id: "gpt-5.6-terra", label: "GPT-5.6 Terra" }],
      source: "live",
      notice: null,
    });
    installDocMindApi({ settings: { listModels } });
    renderSettings();

    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "openai" } });
    fireEvent.click(screen.getByRole("button", { name: "获取模型列表" }));

    await waitFor(() => expect(listModels).toHaveBeenCalled());
    expect(listModels).toHaveBeenLastCalledWith(
      expect.objectContaining({ preset: "openai", baseUrl: "https://api.openai.com/v1" }),
    );
  });

  it("does not claim a curated list came from the provider", async () => {
    installDocMindApi({
      settings: {
        listModels: vi.fn().mockResolvedValue({
          models: [{ id: "mimo-v2.6-flash-free", label: "MiMo V2.6 Flash Free" }],
          source: "curated",
          notice: "未填写 API Key，且该预设没有内置模型列表",
        }),
      },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "获取模型列表" }));

    // The reason must reach the user instead of "已获取 N 个可用模型".
    expect(await screen.findByRole("alert")).toHaveTextContent("未填写 API Key");
    expect(screen.queryByText(/已获取 \d+ 个可用模型/)).toBeNull();
  });

  it("surfaces the reason a live fetch fell back to the built-in list", async () => {
    installDocMindApi({
      settings: {
        listModels: vi.fn().mockResolvedValue({
          models: [{ id: "gpt-5.6-terra", label: "GPT-5.6 Terra" }],
          source: "curated",
          notice: "实时获取失败（API Key 无效），已显示内置列表",
        }),
      },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "获取模型列表" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("API Key 无效");
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
      "REMOTE_LOGIN_REQUIRED",
      "YUQUE_LOGIN_REQUIRED",
      "FEISHU_LOGIN_REQUIRED",
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
