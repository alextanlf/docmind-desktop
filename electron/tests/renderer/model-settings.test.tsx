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
}) {
  return render(
    <ModelSettingsForm
      onConnectionInvalidated={callbacks?.onConnectionInvalidated}
      onConnectionSuccess={callbacks?.onConnectionSuccess}
      settings={readySettings}
    />,
  );
}

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
    expect(test).toBeDisabled();
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

    await act(async () => pendingSave.resolve(readySettings));

    expect(screen.queryByText("设置已保存")).not.toBeInTheDocument();
    expect(test).toBeDisabled();
    expect(model).toHaveValue("newer-change");
  });

  it("prevents overlapping save and test operations", async () => {
    const pendingTest = deferred<ModelConnectionResult>();
    const testModel = vi.fn(() => pendingTest.promise);
    const saveModel = vi.fn().mockResolvedValue(readySettings);
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
    await act(async () => pendingSave.resolve(readySettings));
    await waitFor(() => expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled());
  });
});
