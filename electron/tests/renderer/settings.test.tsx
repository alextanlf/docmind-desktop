import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { SettingsView } from "../../renderer/src/features/settings/SettingsView";
import { settingsModules } from "../../renderer/src/features/settings/settings-modules";
import { clientErrorMessage } from "../../renderer/src/features/settings/settings.queries";
import {
  brokenPlugin,
  installDocMindApi,
  loggedOutRemote,
  pluginManifests,
  readySettings,
  switchedOffPlugin,
  texPluginPath,
} from "./test-docmind-api";

function renderSettings() {
  return render(
    <AppProviders>
      <SettingsView />
    </AppProviders>,
  );
}

/**
 * Open a settings page by its nav label.
 *
 * The settings screen used to be one long column with every section visible at
 * once, so a test could assert against any of them without navigating. It is now
 * a set of separate pages, one visible at a time, so a test that cares about a
 * page has to go there — the same way a user does. Content on a page that is
 * not open is `hidden`, and a hidden element is correctly not "visible".
 */
async function openSection(label: string) {
  // The nav does not exist until the settings snapshot resolves, so wait for
  // the page chrome rather than assuming a synchronous first paint.
  const nav = await screen.findByRole("navigation", { name: "设置分区" });
  fireEvent.click(within(nav).getByRole("button", { name: label }));
}

/**
 * The plugin page's two levels, located the way a user gets between them.
 *
 * The list is a set of rows and the detail is one plugin behind a click, so a
 * test that cares about a credential form has to open the plugin first — the
 * same journey the user makes. Rows are matched on the label they *start* with:
 * a row's accessible name is everything it shows (label, owner, summary,
 * status), and asserting the whole string would break every time a status label
 * changes.
 */
function pluginSection(): HTMLElement {
  return screen.getByRole("heading", { name: "插件" }).closest("section")!;
}

function pluginRows(): HTMLElement[] {
  // `queryAll`: "no rows" is a state these tests assert, and the `getAll`
  // variant throws on it rather than returning an empty list.
  return within(pluginSection()).queryAllByRole("listitem");
}

async function pluginRow(label: string): Promise<HTMLElement> {
  const pattern = new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
  return screen.findByRole("button", { name: pattern });
}

function pluginDetail(title: string): HTMLElement {
  return screen.getByRole("heading", { name: title, level: 3 }).closest(".plugin-detail")!;
}

async function openPlugin(label: string): Promise<HTMLElement> {
  fireEvent.click(await pluginRow(label));
  return pluginDetail(label);
}

async function backToList(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "返回插件列表" }));
  await screen.findByLabelText("搜索插件");
}

function pluginInput(title: string): HTMLElement {
  return within(pluginDetail(title)).getByLabelText(title);
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

  it("stops claiming a key is saved once the vendor that owns it is switched away", async () => {
    installDocMindApi();
    renderSettings();

    // DeepSeek holds the stored key (readySettings.apiKeys.deepseek).
    expect(await screen.findByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "已安全保存，留空可保留",
    );
    expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled();

    fireEvent.change(screen.getByLabelText("模型预设"), { target: { value: "kimi" } });

    // 🔴 回归：key 不是通用的。换一家之后必须改口说「请输入」，并禁用测试连接，
    // 否则用户会以为手里那把 DeepSeek 的 key 能打通 Kimi。
    expect(await screen.findByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "请输入 API Key",
    );
    expect(screen.queryByRole("checkbox", { name: "清除已保存的 API Key" })).toBeNull();
    expect(screen.getByRole("button", { name: "测试连接" })).toBeDisabled();
  });

  it("restores the saved-key indicator when switching back to its own vendor", async () => {
    installDocMindApi();
    renderSettings();

    fireEvent.change(await screen.findByLabelText("模型预设"), { target: { value: "kimi" } });
    expect(await screen.findByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "请输入 API Key",
    );

    // 切回去必须恢复 —— key 还在那把槽里，没被换家时清掉。
    fireEvent.change(screen.getByLabelText("模型预设"), { target: { value: "deepseek" } });
    expect(await screen.findByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "已安全保存，留空可保留",
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

  it("shows exactly one settings page at a time, chosen from the section nav", async () => {
    installDocMindApi();
    renderSettings();

    // One nav entry per registered module — the nav and the page are derived
    // from the same registry, so a page that exists must be reachable and a
    // page that is reachable must exist.
    //
    // 🔴 期望值必须从注册表派生。写死数字的话，删/加一个分区只会让这条失败，
    // 而它本该防的「导航与页面脱钩」反而测不出来（两边都少一个也会通过）。
    const nav = await screen.findByRole("navigation", { name: "设置分区" });
    const expected = settingsModules().length;
    expect(expected).toBeGreaterThan(0);
    expect(within(nav).getAllByRole("button")).toHaveLength(expected);

    const sections = document.querySelectorAll(".settings-section");
    expect(sections).toHaveLength(expected);
    // The opening page is the first registered module, not a hard-coded id.
    // Inactive pages are `hidden`, and a role query skips hidden nodes by
    // design, so asserting on them needs the explicit flag.
    expect(screen.getByRole("heading", { name: "对话模型" }).closest("section")).toBeVisible();
    expect(screen.getByRole("heading", { name: "插件", hidden: true }).closest("section")).not.toBeVisible();

    const target = within(nav).getByRole("button", { name: "本地数据与诊断" });
    fireEvent.click(target);

    expect(target).toHaveAttribute("aria-current", "true");
    const opened = screen.getByRole("heading", { name: "本地数据与诊断" }).closest("section")!;
    expect(opened).toBeVisible();
    expect(opened).toHaveFocus();
    // Switching pages leaves exactly one visible — this is the behaviour that
    // distinguishes separate pages from one long scrolled column.
    expect(
      Array.from(document.querySelectorAll(".settings-section")).filter(
        (section) => !section.hasAttribute("hidden"),
      ),
    ).toHaveLength(1);
  });

  it("keeps an unsaved draft alive while the user visits another page and returns", async () => {
    const api = installDocMindApi();
    renderSettings();

    const key = await screen.findByLabelText("API Key");
    fireEvent.change(key, { target: { value: "sk-not-saved-yet" } });

    await openSection("插件");
    await openSection("对话模型");

    // Unmounting on switch would silently discard a half-entered key, which is
    // the one thing a settings form must never do.
    expect(screen.getByLabelText("API Key")).toHaveValue("sk-not-saved-yet");
    // And nothing was saved by looking around.
    expect(api.settings.saveModel).not.toHaveBeenCalled();
  });

  it("carries a module's own copy rather than renderer-supplied wording", async () => {
    installDocMindApi();
    renderSettings();

    // Each page declares its own description, so the view holds no per-page
    // strings that a new module would have to be added to. Hidden pages are
    // queried with `{ hidden: true }` because a role query deliberately skips
    // them — which is the a11y behaviour we want, not an obstacle to it.
    await screen.findByRole("heading", { name: "对话模型" });
    const modelPage = screen.getByRole("heading", { name: "对话模型" }).closest("section")!;
    const pluginPage = screen.getByRole("heading", { name: "插件", hidden: true }).closest(
      "section",
    )!;

    expect(modelPage).toHaveTextContent("使用 OpenAI 兼容接口连接模型服务");
    expect(pluginPage).not.toHaveTextContent("使用 OpenAI 兼容接口连接模型服务");
    // The plugin page declares no description of its own, so it renders no
    // subtitle line at all rather than inheriting another module's.
    expect(pluginPage.querySelector(".section-heading p")).toBeNull();
  });

  it("keeps the nav and the pages in the same order", async () => {
    installDocMindApi();
    renderSettings();

    await screen.findByRole("navigation", { name: "设置分区" });
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    const navLabels = within(nav)
      .getAllByRole("button")
      .map((button) => button.textContent);
    const pageLabels = Array.from(document.querySelectorAll(".settings-section h2")).map(
      (heading) => heading.textContent,
    );

    // If these ever diverge, the nav points at pages in a different order than
    // they appear — the classic symptom of two hand-maintained lists.
    expect(navLabels).toEqual(pageLabels);
  });

  it("shows remote login controls without any embedding panel", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    renderSettings();
    await openSection("插件");

    // 嵌入模型随应用分发且启动即自动预热，设置页不再暴露它的状态/加载入口。
    expect(screen.queryByText("Embedding 模型")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载模型" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新加载" })).not.toBeInTheDocument();
    expect(api.embedding.prepare).not.toHaveBeenCalled();
    expect(screen.queryByRole("progressbar", { name: "Embedding 加载进度" })).not.toBeInTheDocument();

    // The login control is one level in now: the list says what a plugin is for,
    // the detail is where it is connected.
    const detail = await openPlugin("语雀网页登录");
    const login = await within(detail).findByRole("button", { name: "登录语雀网页登录" });
    expect(login).toBeVisible();
    fireEvent.click(login);
    expect(api.remote.login).toHaveBeenCalledWith("yuque");
  });

  it("lists one row per plugin with no grouping and no vendor list", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    renderSettings();
    await openSection("插件");

    // One row per credential channel — 「语雀网页登录」 and 「语雀 API」 are two
    // things a user chooses between, so they are two rows rather than one vendor
    // row with two lines in it.
    await pluginRow("语雀网页登录");
    for (const label of ["语雀 API", "飞书自建应用", "飞书账号授权", "飞书机器人"]) {
      expect(await pluginRow(label)).toBeVisible();
    }
    expect(pluginRows()).toHaveLength(pluginManifests.length);

    // The regression this page exists to prevent: rows used to be partitioned
    // into 「知识库来源」/「通知」, which made a bot webhook read as a third way
    // to import documents. There must be no such sections anywhere on the page.
    expect(screen.queryByRole("region", { name: "知识库来源" })).toBeNull();
    expect(screen.queryByRole("region", { name: "通知" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "知识库来源" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "通知" })).toBeNull();
    // And no sub-headings inside the plugin page itself: every row is a peer,
    // with nothing above it but the search box. Scoped to the section because
    // other settings pages legitimately have their own sub-headings.
    expect(within(pluginSection()).queryAllByRole("heading", { level: 4 })).toHaveLength(0);
    expect(within(pluginSection()).queryAllByRole("region")).toHaveLength(0);

    // A single request for the whole catalogue. The old layout needed one
    // credential request per provider just to decide its groupings.
    expect(api.plugins.list).toHaveBeenCalledTimes(1);
    expect(api.remote.listProviders).not.toHaveBeenCalled();

    // Each row says which integration it plugs into and what it is for. Without
    // the owner a row titled 「机器人」 or 「网页登录」 identifies nothing.
    const row = await pluginRow("飞书机器人");
    expect(within(row).getByText("飞书文档")).toBeVisible();
    expect(within(row).getByText("导入完成或失败时，往群里发一条通知")).toBeVisible();
    // Purpose survives as a tag, not as a section divider.
    expect(within(row).getByText("通知")).toBeVisible();
    expect(within(await pluginRow("语雀 API")).getByText("知识库")).toBeVisible();
  });

  it("describes a contributed document format without inventing a credential", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    await pluginRow("TeX 文档");
    const detail = await openPlugin("TeX 文档");

    // A format states what it adds rather than offering a credential form.
    expect(within(detail).getByText("文档格式")).toBeVisible();
    expect(within(detail).getByText("支持的文件类型")).toBeVisible();
    expect(within(detail).getByText(".tex")).toBeVisible();
    expect(within(detail).getByText(".latex")).toBeVisible();
    // It stores no credential, so neither a secret field nor a login button
    // belongs on it — the old two-way body would have drawn a login button.
    expect(within(detail).queryByRole("button", { name: /登录/ })).toBeNull();
    expect(within(detail).queryByRole("textbox")).toBeNull();
    // And it reports no connection, because there is nothing to connect.
    expect(within(detail).queryByText("未连接")).toBeNull();
  });

  it("finds a format row through the search box by its extension", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    await pluginRow("语雀网页登录");
    fireEvent.change(screen.getByLabelText("搜索插件"), { target: { value: ".latex" } });

    expect(await pluginRow("TeX 文档")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^语雀 API/ })).toBeNull();
  });

  it("searches plugins by label, owner, summary and declared keywords", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    await pluginRow("语雀网页登录");
    const search = screen.getByLabelText("搜索插件");

    // All three Feishu channels match on the owner alone, across all three
    // rows — the owner is searchable, not just the title.
    fireEvent.change(search, { target: { value: "飞书" } });
    expect(await pluginRow("飞书机器人")).toBeVisible();
    expect(await pluginRow("飞书自建应用")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^语雀 API/ })).toBeNull();

    // An alias only the plugin declares:「lark」appears in no label at all.
    fireEvent.change(search, { target: { value: "lark" } });
    expect(pluginRows()).toHaveLength(3);

    // The summary is searchable, which is why a plugin whose summary mentions a
    // token matches on it too — 「语雀 API」 declares「用个人访问令牌读取…」.
    fireEvent.change(search, { target: { value: "访问令牌" } });
    expect(await pluginRow("Acme 访问令牌")).toBeVisible();
    expect(await pluginRow("语雀 API")).toBeVisible();
    // A summary that mentions no plugin at all — nothing to match.
    expect(screen.queryByRole("button", { name: /^飞书机器人/ })).toBeNull();

    // Terms are ANDed, so a second word narrows instead of widening.
    fireEvent.change(search, { target: { value: "语雀 令牌" } });
    expect(await pluginRow("语雀 API")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^语雀网页登录/ })).toBeNull();

    // A miss explains itself instead of showing an empty page.
    fireEvent.change(search, { target: { value: "不存在的东西" } });
    expect(pluginRows()).toHaveLength(0);
    expect(screen.getByText(/换个关键词试试/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(pluginRows().length).toBeGreaterThan(3);
  });

  it("finds a plugin by its own name, which no label contains", async () => {
    // 「docmind-tex」 is what someone who cloned it calls it, and the name appears
    // in no label on the page — it reaches the search through the provenance.
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    await pluginRow("语雀网页登录");
    fireEvent.change(screen.getByLabelText("搜索插件"), { target: { value: "docmind-tex" } });

    expect(await pluginRow("TeX 文档")).toBeVisible();
    expect(pluginRows()).toHaveLength(1);
  });

  it("presents an installed third-party plugin with no vendor-specific code", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    //「acme」 exists nowhere in DocMind's own source. If this row renders, the
    // page really is driven by the catalogue rather than by a hard-coded list.
    await pluginRow("Acme 访问令牌");
    const detail = await openPlugin("Acme 访问令牌");

    expect(within(detail).getByText("Acme Wiki")).toBeVisible();
    expect(within(detail).getByText("用个人访问令牌读取 Acme Wiki")).toBeVisible();
    // Declared version, homepage and category all surface.
    expect(within(detail).getByText("v0.1.0")).toBeVisible();
    expect(within(detail).getByRole("link", { name: "了解更多" })).toBeVisible();
    // And where it came from, which the card could not know: it is installed as
    // a package, so it can be switched off but not removed from here.
    expect(within(detail).getByText("已安装的插件包")).toBeVisible();
    expect(within(detail).getByRole("button", { name: "停用插件" })).toBeVisible();
    expect(within(detail).queryByRole("button", { name: "移除插件" })).toBeNull();
    expect(
      within(detail).getByText("这个插件以插件包的形式安装，DocMind 不能替你移除它。"),
    ).toBeVisible();
  });

  it("keeps a plugin that failed to load visible, with the reason", async () => {
    installDocMindApi({
      plugins: { list: vi.fn().mockResolvedValue([...pluginManifests, brokenPlugin]) },
    });
    renderSettings();
    await openSection("插件");

    // A plugin that silently failed is indistinguishable from one that was never
    // installed, so the row carries the reason rather than only the name — and
    // it stays reachable so the user can remove the clone that broke.
    const row = await pluginRow("docmind-heavy");
    expect(within(row).getByText(/docmind_absent_dependency/)).toBeVisible();
    expect(within(row).getByText("加载失败")).toBeVisible();

    const detail = await openPlugin("docmind-heavy");
    expect(within(detail).getByRole("alert")).toHaveTextContent("ImportError");
    expect(within(detail).getByText("插件目录")).toBeVisible();
  });

  it("names a directory only for a plugin that came from one", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    const installed = await openPlugin("Acme 访问令牌");
    expect(within(installed).queryByText(/\/tmp\//)).toBeNull();
    await backToList();

    // A directory is the one source a user can act on, so it is the one that
    // gets named — it is the thing they cloned and the thing they need to edit.
    const checkout = await openPlugin("TeX 文档");
    expect(within(checkout).getByText(texPluginPath)).toBeVisible();
  });

  it("tells the user where to put a plugin", async () => {
    // Installing a plugin is a filesystem action, so the path is the one part of
    // the flow the user cannot find in the UI. It comes from the server: it is
    // derived from the data directory, which is per-platform and follows the
    // application's own name, so the renderer cannot compose it.
    const pluginsDirectory = "/Users/x/Library/Application Support/docmind-desktop/plugins";
    installDocMindApi({
      plugins: { directory: vi.fn().mockResolvedValue({ path: pluginsDirectory }) },
    });
    renderSettings();
    await openSection("插件");

    expect(await screen.findByText(pluginsDirectory)).toBeVisible();
    expect(screen.getByText(/重启 DocMind 后生效/)).toBeVisible();
  });

  it("keeps the search when a plugin is opened and the list is returned to", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    await pluginRow("语雀网页登录");
    fireEvent.change(screen.getByLabelText("搜索插件"), { target: { value: "飞书" } });
    await openPlugin("飞书机器人");

    // The list unmounts while a detail is open, so a search box that owned its
    // own text would come back empty — and the user would have to type the same
    // thing again to get back to where they were.
    await backToList();
    expect(screen.getByLabelText("搜索插件")).toHaveValue("飞书");
    expect(pluginRows()).toHaveLength(3);
  });

  it("saves a plugin credential under its own provider and channel", async () => {
    const api = installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
    });
    renderSettings();
    await openSection("插件");

    // Located through the plugin it belongs to, not by index: the list order is
    // whatever the catalogue returned, and an index would silently start
    // pointing at a different plugin's form the moment one is added or removed.
    const feishu = await openPlugin("飞书自建应用");
    const field = pluginInput("飞书自建应用");
    fireEvent.change(field, { target: { value: "cli_a1b2:s3cret" } });
    fireEvent.click(within(feishu).getByRole("button", { name: "保存并验证" }));

    await waitFor(() =>
      expect(api.remote.saveCredential).toHaveBeenCalledWith("feishu", "app", {
        secret: "cli_a1b2:s3cret",
      }),
    );
    expect(api.remote.testCredential).toHaveBeenCalledWith("feishu", "app");

    await backToList();
    const yuque = await openPlugin("语雀 API");
    fireEvent.change(pluginInput("语雀 API"), { target: { value: "yuque-token" } });
    fireEvent.click(within(yuque).getByRole("button", { name: "保存并验证" }));
    await waitFor(() =>
      expect(api.remote.saveCredential).toHaveBeenCalledWith("yuque", "api", {
        secret: "yuque-token",
      }),
    );
    expect(api.remote.testCredential).toHaveBeenCalledWith("yuque", "api");
  });

  it("derives the plugin count from the catalogue", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    // Regression: the description used to be a static sentence naming vendors,
    // so it went stale the moment a plugin was added or removed. The count is
    // read off the fixture rather than written out, so this asserts the count
    // is derived and not that the catalogue happens to hold a particular
    // number today.
    expect(
      await screen.findByText(new RegExp(`共 ${pluginManifests.length} 个插件`)),
    ).toBeVisible();
    expect(screen.getByText(/均为可选连接/)).toBeVisible();
  });

  it("falls back to neutral copy when no plugin is installed", async () => {
    installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
      plugins: { list: vi.fn().mockResolvedValue([]) },
    });
    renderSettings();
    await openSection("插件");

    expect(await screen.findByText("当前没有可用插件。")).toBeVisible();
    // The search box stays: an empty catalogue is a state, not a dead page.
    expect(screen.getByLabelText("搜索插件")).toBeVisible();
  });

  it("renders channel-declared hints instead of inventing provider copy", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    // A URL-shaped credential must not be labelled "… Token", and the form
    // has to surface the plugin's own placeholder and help link.
    const webhook = await openPlugin("飞书机器人");
    const webhookInput = pluginInput("飞书机器人");
    expect(webhookInput).toHaveAttribute(
      "placeholder",
      "https://open.feishu.cn/open-apis/bot/v2/hook/…",
    );
    // The help link comes from the plugin manifest rather than any hard-coded
    // vendor string, so a third-party plugin's link renders the same way.
    expect(within(webhook).getAllByRole("link", { name: /添加机器人/ })).toHaveLength(1);
    // Plugins supply their own placeholder too, instead of the generic copy.
    await backToList();
    await openPlugin("语雀 API");
    expect(pluginInput("语雀 API")).toHaveAttribute("placeholder", "粘贴语雀个人访问令牌");
  });

  it("picks the right form per plugin from its own hasSecret declaration", async () => {
    // A plugin needing a secret renders a credential field; one that does not
    // renders the login entry. Deciding this in the renderer from a per-plugin
    // flag keeps a plugin with no secret form (an OAuth-only integration) from
    // being handed an empty text box it cannot use.
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    // hasSecret: false → the login affordance and no credential field at all.
    // The login entry appears only after the plugin's status query lands, so it
    // is awaited rather than assumed.
    const web = await openPlugin("语雀网页登录");
    expect(await within(web).findByRole("button", { name: "登录语雀网页登录" })).toBeVisible();
    expect(within(web).queryByLabelText("语雀网页登录")).toBeNull();

    // hasSecret: true → the field, no login button.
    await backToList();
    const app = await openPlugin("飞书自建应用");
    expect(pluginInput("飞书自建应用")).toBeVisible();
    expect(within(app).queryByRole("button", { name: /登录飞书自建应用/ })).toBeNull();

    // The help link belongs to the channel that declares one, not to every
    // channel of that provider.
    await backToList();
    const api = await openPlugin("语雀 API");
    expect(within(api).getAllByRole("link", { name: /获取令牌/ })).toHaveLength(1);
    await backToList();
    const other = await openPlugin("飞书账号授权");
    expect(within(other).queryByRole("link", { name: /获取令牌/ })).toBeNull();
  });

  it("installs the browser when a plugin declares it and login reports it missing", async () => {
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
    await openSection("插件");

    // The affordance exists because the manifest declares browserInstall plus
    // the error code its absence raises — not because the vendor is recognised.
    const detail = await openPlugin("语雀网页登录");
    fireEvent.click(await within(detail).findByRole("button", { name: "登录语雀网页登录" }));
    fireEvent.click(await screen.findByRole("button", { name: "安装浏览器" }));

    expect(await screen.findByText("语雀浏览器已安装")).toBeVisible();
    expect(api.remote.installBrowser).toHaveBeenCalledWith("yuque");
  });

  it("offers no browser install to a plugin that did not declare it", async () => {
    installDocMindApi({
      remote: {
        status: vi.fn().mockRejectedValue({
          code: "SOMETHING_ELSE",
          message: "不可用",
          retryable: false,
        }),
        login: vi.fn().mockRejectedValue({
          code: "SOMETHING_ELSE",
          message: "不可用",
          retryable: false,
        }),
      },
    });
    renderSettings();
    await openSection("插件");

    // browserInstall is false for every Feishu channel, so the button must not
    // appear even when login fails — a vendor-name check would have shown it.
    // The channel is one that renders a login entry, since that is the only
    // form the affordance belongs to.
    const detail = await openPlugin("飞书账号授权");
    expect((await within(detail).findAllByRole("button", { name: "重新检查" })).length).toBeGreaterThan(0);
    expect(within(detail).queryByRole("button", { name: "安装浏览器" })).toBeNull();
  });

  it("switches a plugin off and says when that takes effect", async () => {
    const api = installDocMindApi({ plugins: { list: vi.fn().mockResolvedValue([...pluginManifests, switchedOffPlugin]) } });
    renderSettings();
    await openSection("插件");

    // A plugin with no cards of its own is still a row, marked as switched off:
    // the alternative is a plugin that vanishes when turned off, which could
    // never be turned back on.
    const row = await pluginRow("docmind-pages");
    expect(within(row).getByText("已停用")).toBeVisible();
    expect(within(row).getByText("把 Apple Pages 文档导入 DocMind")).toBeVisible();

    const detail = await openPlugin("docmind-pages");
    expect(within(detail).getByText("/tmp/docmind-data/plugins/docmind-pages")).toBeVisible();
    expect(
      within(detail).getByText("已停用，重启 DocMind 后生效。"),
    ).toBeVisible();

    fireEvent.click(within(detail).getByRole("button", { name: "启用插件" }));

    await waitFor(() => expect(api.plugins.setEnabled).toHaveBeenCalledWith("docmind-pages", true));
  });

  it("switches a loaded plugin off", async () => {
    const api = installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    const detail = await openPlugin("TeX 文档");
    fireEvent.click(within(detail).getByRole("button", { name: "停用插件" }));

    await waitFor(() => expect(api.plugins.setEnabled).toHaveBeenCalledWith("docmind-tex", false));
  });

  it("shows the switch in its new position as soon as the catalogue says so", async () => {
    // The backend reflects the switch in the catalogue it serves — the plugin
    // still runs until the restart, but it is no longer announced as on — so the
    // page must not need a restart to show the new position either. Otherwise
    // the user clicks 停用, the button does not move, and it reads as broken.
    const switchedOff = pluginManifests.map((plugin) =>
      plugin.id === "tex:core"
        ? { ...plugin, origin: { ...plugin.origin!, enabled: false, active: false } }
        : plugin,
    );
    installDocMindApi({
      remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) },
      plugins: {
        list: vi.fn().mockResolvedValueOnce(pluginManifests).mockResolvedValue(switchedOff),
      },
    });
    renderSettings();
    await openSection("插件");

    const detail = await openPlugin("TeX 文档");
    fireEvent.click(within(detail).getByRole("button", { name: "停用插件" }));

    const after = pluginDetail("TeX 文档");
    expect(await within(after).findByRole("button", { name: "启用插件" })).toBeVisible();
    expect(within(after).getByText("已停用，重启 DocMind 后生效。")).toBeVisible();
    expect(within(after).getByText("已停用")).toBeVisible();
  });

  it("offers a built-in plugin no switch, and says why", async () => {
    installDocMindApi({ remote: { status: vi.fn().mockResolvedValue(loggedOutRemote) } });
    renderSettings();
    await openSection("插件");

    // DocMind's own integrations are installed by the application rather than
    // found on disk, so there is nothing here to switch off or move. Stated,
    // because a missing control with no explanation reads as a bug.
    const detail = await openPlugin("语雀 API");
    expect(within(detail).queryByRole("button", { name: "停用插件" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: "启用插件" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: "移除插件" })).toBeNull();
    expect(within(detail).getByText("随 DocMind 提供，不能单独停用或移除。")).toBeVisible();
    expect(within(detail).getByText("随 DocMind 提供")).toBeVisible();
  });

  it("removes a directory plugin only after a confirmation that says it is not a delete", async () => {
    const api = installDocMindApi({ plugins: { list: vi.fn().mockResolvedValue([...pluginManifests, switchedOffPlugin]) } });
    renderSettings();
    await openSection("插件");

    const detail = await openPlugin("docmind-pages");
    fireEvent.click(within(detail).getByRole("button", { name: "移除插件" }));

    const dialog = await screen.findByRole("dialog", { name: "确认移除插件" });
    // The confirmation has to say the files survive: "remove" reads as "delete"
    // otherwise, and this moves a checkout the user cloned themselves.
    expect(dialog).toHaveTextContent("插件文件不会被删除");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认移除" }));

    await waitFor(() => expect(api.plugins.uninstall).toHaveBeenCalledWith("docmind-pages"));
    // The row is gone, so the message belongs to the list the user lands on —
    // and it names where the plugin went rather than claiming it was deleted.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "/tmp/docmind-data/removed-plugins/docmind-pages",
    );
    expect(screen.getByLabelText("搜索插件")).toBeVisible();
  });

  it("cannot remove a plugin without confirming", async () => {
    const api = installDocMindApi({ plugins: { list: vi.fn().mockResolvedValue([...pluginManifests, switchedOffPlugin]) } });
    renderSettings();
    await openSection("插件");

    const detail = await openPlugin("docmind-pages");
    fireEvent.click(within(detail).getByRole("button", { name: "移除插件" }));
    const dialog = await screen.findByRole("dialog", { name: "确认移除插件" });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));

    expect(api.plugins.uninstall).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "确认移除插件" })).not.toBeInTheDocument();
  });

  it("reports a refused action instead of pretending it worked", async () => {
    const api = installDocMindApi({
      plugins: {
        list: vi.fn().mockResolvedValue([...pluginManifests, switchedOffPlugin]),
        setEnabled: vi.fn().mockRejectedValue({
          code: "PLUGIN_NOT_TOGGLEABLE",
          message: "随 DocMind 提供，不能停用",
          retryable: false,
        }),
      },
    });
    renderSettings();
    await openSection("插件");

    const detail = await openPlugin("docmind-pages");
    fireEvent.click(within(detail).getByRole("button", { name: "启用插件" }));

    expect(await within(pluginDetail("docmind-pages")).findByRole("alert")).toBeVisible();
    // And the switch is still where it was: a failed write must not look applied.
    expect(
      within(pluginDetail("docmind-pages")).getByRole("button", { name: "启用插件" }),
    ).toBeVisible();
    expect(api.plugins.setEnabled).toHaveBeenCalledTimes(1);
  });

  it("confirms and clears only diagnostic screenshots, then refetches settings", async () => {
    const get = vi.fn().mockResolvedValue(readySettings);
    const api = installDocMindApi({ settings: { get } });
    renderSettings();
    await openSection("本地数据与诊断");

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
    await openSection("本地数据与诊断");

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
    await openSection("本地数据与诊断");
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
