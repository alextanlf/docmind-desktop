import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ModelConnectionResult, SettingsView } from "../../shared/contracts";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ModelSettingsForm } from "../../renderer/src/features/settings/ModelSettingsForm";
import { installDocMindApi, readySettings } from "./test-docmind-api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderModelSettings(callbacks?: {
  onConnectionSuccess?: () => void;
  onConnectionInvalidated?: () => void;
  profile?: (typeof readySettings.models)[number] | null;
}) {
  return render(
    <ModelSettingsForm
      onConnectionInvalidated={callbacks?.onConnectionInvalidated}
      onConnectionSuccess={callbacks?.onConnectionSuccess}
      profile={callbacks?.profile ?? null}
      // These cases drive the free-text path on purpose: they need to set
      // arbitrary model ids mid-operation to prove stale results are dropped.
      settings={freeTextSettings}
    />,
  );
}

// These cases exercise the free-text path on purpose: they set arbitrary model
// ids mid-operation to prove stale results are dropped. The real catalogue
// would render a <select>, which cannot hold those ids.
const freeTextSettings: SettingsView = { ...readySettings, modelPresets: {} };

describe("模型设置并发", () => {
  beforeEach(() => appQueryClient.clear());

  it("locks edits and save while testing and ignores a stale result after a change event", async () => {
    const pendingTest = deferred<ModelConnectionResult>();
    const onConnectionSuccess = vi.fn();
    installDocMindApi({ settings: { testModel: vi.fn(() => pendingTest.promise) } });
    renderModelSettings({ onConnectionSuccess });

    const model = screen.getByLabelText("模型名称");
    const save = screen.getByRole("button", { name: "保存设置" });
    const test = screen.getByRole("button", { name: "测试连接" });
    fireEvent.click(test);

    expect(model).toBeDisabled();
    expect(save).toBeDisabled();
    expect(test).toBeDisabled();
    fireEvent.change(model, { target: { value: "changed-during-test" } });

    await act(async () => pendingTest.resolve({ connected: true, latencyMs: 42 }));

    expect(onConnectionSuccess).not.toHaveBeenCalled();
    expect(screen.queryByText(/连接成功/)).not.toBeInTheDocument();
    expect(test).toBeEnabled();
  });

  it("keeps edits dirty when an older save resolves", async () => {
    const pendingSave = deferred<SettingsView>();
    installDocMindApi({ settings: { saveModel: vi.fn(() => pendingSave.promise) } });
    renderModelSettings();

    const model = screen.getByLabelText("模型名称");
    const save = screen.getByRole("button", { name: "保存设置" });
    const test = screen.getByRole("button", { name: "测试连接" });
    fireEvent.change(model, { target: { value: "first-change" } });
    fireEvent.click(save);

    expect(model).toBeDisabled();
    expect(save).toBeDisabled();
    expect(test).toBeDisabled();
    fireEvent.change(model, { target: { value: "newer-change" } });

    await act(async () => pendingSave.resolve(freeTextSettings));

    expect(screen.queryByText("设置已保存")).not.toBeInTheDocument();
    expect(model).toHaveValue("newer-change");
  });

  it("prevents overlapping save and test operations", async () => {
    const pendingTest = deferred<ModelConnectionResult>();
    const testModel = vi.fn(() => pendingTest.promise);
    const saveModel = vi.fn().mockResolvedValue(freeTextSettings);
    installDocMindApi({ settings: { saveModel, testModel } });
    const first = renderModelSettings();

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    expect(testModel).toHaveBeenCalledTimes(1);
    expect(saveModel).not.toHaveBeenCalled();
    await act(async () => pendingTest.resolve({ connected: true, latencyMs: 21 }));
    first.unmount();

    const pendingSave = deferred<SettingsView>();
    const secondTestModel = vi.fn().mockResolvedValue({ connected: true, latencyMs: 21 });
    const secondSaveModel = vi.fn(() => pendingSave.promise);
    installDocMindApi({
      settings: { saveModel: secondSaveModel, testModel: secondTestModel },
    });
    renderModelSettings();
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "changed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));

    expect(secondSaveModel).toHaveBeenCalledTimes(1);
    expect(secondTestModel).not.toHaveBeenCalled();
    await act(async () => pendingSave.resolve(freeTextSettings));
    await waitFor(() => expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled());
  });

  it("edits the opened profile instead of whatever happens to be in use", async () => {
    const api = installDocMindApi();
    const relay = {
      id: "relay01",
      preset: "custom:relay01",
      label: "某中转站",
      baseUrl: "https://relay.example/v1",
      model: "relay-model",
      timeoutSeconds: 45,
      reasoningEffort: "",
      active: false,
      hasApiKey: false,
    };
    renderModelSettings({ profile: relay });

    // 表单带的是被点开的那一条，而不是当前使用的那条（freeTextSettings 里那条是
    // DeepSeek），保存也要带它的档案 id —— 否则「编辑」会变成再建一条。
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://relay.example/v1");
    expect(screen.getByLabelText("模型名称")).toHaveValue("relay-model");
    expect(screen.getByLabelText("供应商名称")).toHaveValue("某中转站");

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(api.settings.saveModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ profileId: "relay01", preset: "custom:relay01" }),
      ),
    );
  });

  it("tests the draft, not the saved model", async () => {
    const testModel = vi.fn().mockResolvedValue({ connected: true, latencyMs: 12 });
    installDocMindApi({ settings: { testModel } });
    renderModelSettings();

    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "draft-model" } });
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));

    await waitFor(() =>
      expect(testModel).toHaveBeenLastCalledWith(
        expect.objectContaining({ preset: "deepseek", model: "draft-model" }),
      ),
    );
  });
});
