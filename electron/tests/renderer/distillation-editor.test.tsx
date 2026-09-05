import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Distillation, DistillationEdit } from "../../shared/contracts";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DistillationEditor } from "../../renderer/src/features/memory/DistillationEditor";
import { installDocMindApi, repository } from "./test-docmind-api";

function delayed<Value>() {
  let resolve!: (value: Value) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Value>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function setup(state: Distillation["state"] = "draft") {
  let stored: Distillation = { ...draft, state };
  const pending: ReturnType<typeof delayed<Distillation>>[] = [];
  const updateDistillation = vi.fn(async (_id: string, edit: DistillationEdit) => {
    const request = delayed<Distillation>();
    pending.push(request);
    await request.promise;
    stored = { ...stored, ...edit };
    return stored;
  });
  const getDistillation = vi.fn(async (id: string) => ({ ...stored, id }));
  const saveDistillation = vi.fn(async () => stored);
  const regenerateDistillation = vi.fn(async () => {
    stored = { ...stored, title: "Generated", content: "Generated body", keyPoints: ["Generated point"] };
    return stored;
  });
  installDocMindApi({ memory: { getDistillation, updateDistillation, saveDistillation, regenerateDistillation } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>);
  return { ...view, client, pending, updateDistillation, saveDistillation, regenerateDistillation, readStored: () => stored };
}

it.each([false, true])("orders remounted same-ID edits after old queued work, predecessor failure=%s", async (failFirst) => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "A" } });
  fireEvent.blur(title);
  await waitFor(() => expect(api.pending).toHaveLength(1));
  fireEvent.change(title, { target: { value: "B" } });
  fireEvent.blur(title);
  api.unmount();
  render(<QueryClientProvider client={api.client}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>);
  const nextTitle = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(nextTitle, { target: { value: "C" } });
  fireEvent.blur(nextTitle);
  await act(async () => {
    if (failFirst) api.pending[0].reject(new Error("A failed"));
    else api.pending[0].resolve(draft);
  });
  await waitFor(() => expect(api.pending.length).toBeGreaterThanOrEqual(2));
  expect(nextTitle).toHaveValue("C");
  await act(async () => api.pending[1].resolve(draft));
  await waitFor(() => expect(api.pending).toHaveLength(3));
  expect(nextTitle).toHaveValue("C");
  await act(async () => api.pending[2].resolve(draft));
  await waitFor(() => expect(api.readStored().title).toBe("C"));
  expect(api.updateDistillation.mock.calls.map(([, edit]) => edit.title)).toEqual(["A", "B", "C"]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retains a confirmed save on unmount ahead of a new instance operation", async () => {
  const api = setup();
  fireEvent.change(await screen.findByLabelText("蒸馏标题"), { target: { value: "Confirmed" } });
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.pending).toHaveLength(1));
  api.unmount();
  render(<QueryClientProvider client={api.client}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "重新生成" }));
  await act(async () => api.pending[0].resolve(draft));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(api.regenerateDistillation).toHaveBeenCalledTimes(1));
  expect(api.saveDistillation.mock.invocationCallOrder[0]).toBeLessThan(api.regenerateDistillation.mock.invocationCallOrder[0]);
});

it("does not block another ID behind an outstanding update", async () => {
  const api = setup();
  fireEvent.change(await screen.findByLabelText("蒸馏标题"), { target: { value: "Pending" } });
  fireEvent.blur(screen.getByLabelText("蒸馏标题"));
  await waitFor(() => expect(api.pending).toHaveLength(1));
  api.unmount();
  const nextId = "00000000-0000-0000-0000-000000000027";
  render(<QueryClientProvider client={api.client}><DistillationEditor distillationId={nextId} /></QueryClientProvider>);
  fireEvent.change(await screen.findByLabelText("蒸馏标题"), { target: { value: "Independent" } });
  fireEvent.blur(screen.getByLabelText("蒸馏标题"));
  await waitFor(() => expect(api.pending).toHaveLength(2));
  await act(async () => api.pending[1].resolve(draft));
  expect(api.updateDistillation).toHaveBeenLastCalledWith(nextId, expect.objectContaining({ title: "Independent" }));
  await act(async () => api.pending[0].resolve(draft));
});

it("serializes full edits and holds a double-click save behind every blur", async () => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "Latest title" } });
  fireEvent.blur(title);
  await waitFor(() => expect(api.pending).toHaveLength(1));
  const body = screen.getByLabelText("蒸馏正文");
  fireEvent.change(body, { target: { value: "Latest body" } });
  fireEvent.blur(body);
  fireEvent.change(screen.getByLabelText("关键要点"), { target: { value: "Latest point" } });
  fireEvent.blur(screen.getByLabelText("关键要点"));
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  expect(api.updateDistillation).toHaveBeenCalledTimes(1);
  expect(api.saveDistillation).not.toHaveBeenCalled();
  await act(async () => api.pending[0].resolve(draft));
  await waitFor(() => expect(api.pending).toHaveLength(2));
  expect(api.saveDistillation).not.toHaveBeenCalled();
  await act(async () => api.pending[1].resolve(draft));
  await waitFor(() => expect(api.pending).toHaveLength(3));
  expect(api.saveDistillation).not.toHaveBeenCalled();
  await act(async () => api.pending[2].resolve(draft));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledTimes(1));
  expect(api.updateDistillation).toHaveBeenLastCalledWith(draft.id, { title: "Latest title", content: "Latest body", keyPoints: ["Latest point"] });
});

it("does not submit when the save flush fails and permits retry", async () => {
  const api = setup();
  fireEvent.change(await screen.findByLabelText("蒸馏标题"), { target: { value: "Flush title" } });
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.pending).toHaveLength(1));
  await act(async () => api.pending[0].reject(new Error("Flush failed")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Flush failed");
  expect(api.saveDistillation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.pending).toHaveLength(2));
  await act(async () => api.pending[1].resolve(draft));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledTimes(1));
});

it.each(["保存知识", "重新生成"])("catches %s rejection and permits retry without redundant update", async (action) => {
  const api = setup();
  await screen.findByLabelText("蒸馏标题");
  const mutation = action === "保存知识" ? api.saveDistillation : api.regenerateDistillation;
  mutation.mockRejectedValueOnce(new Error("Action unavailable"));
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: action }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Action unavailable");
  fireEvent.click(screen.getByRole("button", { name: action }));
  await waitFor(() => expect(mutation).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(api.updateDistillation).not.toHaveBeenCalled();
});

it("isolates a pending old-ID update from the new editor", async () => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "Old ID edit" } });
  fireEvent.blur(title);
  await waitFor(() => expect(api.pending).toHaveLength(1));
  const nextId = "00000000-0000-0000-0000-000000000027";
  api.rerender(<QueryClientProvider client={api.client}><DistillationEditor distillationId={nextId} /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("蒸馏标题")).toHaveValue("Draft"));
  await act(async () => api.pending[0].reject(new Error("Old failure")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledWith(nextId, { target: "local" }));
  expect(api.updateDistillation).toHaveBeenCalledTimes(1);
});

it("preserves another dirty field across an update refetch and flushes without blur", async () => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "Edited title" } });
  fireEvent.blur(title);
  await waitFor(() => expect(api.pending).toHaveLength(1));
  fireEvent.change(screen.getByLabelText("蒸馏正文"), { target: { value: "Still typing" } });
  await act(async () => api.pending[0].resolve(draft));
  expect(screen.getByLabelText("蒸馏正文")).toHaveValue("Still typing");
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.pending).toHaveLength(2));
  expect(api.saveDistillation).not.toHaveBeenCalled();
  await act(async () => api.pending[1].resolve(draft));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledTimes(1));
});

it.each(["保存知识", "重新生成"])("shows update failure and retries before %s", async (action) => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "Retry title" } });
  fireEvent.blur(title);
  await waitFor(() => expect(api.pending).toHaveLength(1));
  await act(async () => api.pending[0].reject(new Error("Update unavailable")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Update unavailable");
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: action }));
  await waitFor(() => expect(api.pending).toHaveLength(2));
  expect(api.saveDistillation).not.toHaveBeenCalled();
  expect(api.regenerateDistillation).not.toHaveBeenCalled();
  await act(async () => api.pending[1].resolve(draft));
  await waitFor(() => expect(action === "保存知识" ? api.saveDistillation : api.regenerateDistillation).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  if (action === "重新生成") expect(screen.getByLabelText("蒸馏正文")).toHaveValue("Generated body");
});

it.each(["saved", "saved_unindexed"] as const)("does not update %s when retrying save", async (state) => {
  const api = setup(state);
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.blur(title);
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  fireEvent.click(screen.getByRole("button", { name: "保存知识" }));
  await waitFor(() => expect(api.saveDistillation).toHaveBeenCalledTimes(1));
  expect(api.updateDistillation).not.toHaveBeenCalled();
});

it("resets unsaved edits and target on ID switch and remount", async () => {
  const api = setup();
  const title = await screen.findByLabelText("蒸馏标题");
  fireEvent.change(title, { target: { value: "Unsaved" } });
  fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
  api.rerender(<QueryClientProvider client={api.client}><DistillationEditor distillationId="00000000-0000-0000-0000-000000000027" /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("蒸馏标题")).toHaveValue("Draft"));
  expect(screen.getByRole("radio", { name: "仅本地" })).not.toBeChecked();
  api.unmount();
  render(<QueryClientProvider client={api.client}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("蒸馏标题")).toHaveValue("Draft"));
  expect(screen.getByRole("button", { name: "保存知识" })).toBeDisabled();
});

const draft = { id: "00000000-0000-0000-0000-000000000026", sessionId: "00000000-0000-0000-0000-000000000025", title: "Draft", content: "# Draft", keyPoints: [], sources: [], repositoryIds: [repository.id], state: "draft" as const, storageTarget: null, localPath: null, documentId: null, yuqueUrl: null, errorCode: null, retryable: false, createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z" };
describe("DistillationEditor", () => { it("updates a draft and requires a save target", async () => { const updateDistillation = vi.fn().mockResolvedValue(draft); const saveDistillation = vi.fn().mockResolvedValue({ ...draft, state: "saved" }); installDocMindApi({ memory: { getDistillation: vi.fn().mockResolvedValue(draft), updateDistillation, saveDistillation } }); render(<QueryClientProvider client={new QueryClient()}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>); const user = userEvent.setup(); const body = await screen.findByLabelText("蒸馏正文"); await user.clear(body); await user.type(body, "# Edited"); await user.tab(); expect(updateDistillation).toHaveBeenCalledWith(draft.id, expect.objectContaining({ content: "# Edited" })); expect(screen.getByRole("button", { name: "保存知识" })).toBeDisabled(); await user.click(screen.getByRole("radio", { name: "仅本地" })); await user.click(screen.getByRole("button", { name: "保存知识" })); expect(saveDistillation).toHaveBeenCalledWith(draft.id, { target: "local" }); }); });

it("waits for the latest draft update before saving", async () => {
  let finishUpdate!: () => void;
  const updateDistillation = vi.fn(() => new Promise<typeof draft>((resolve) => { finishUpdate = () => resolve({ ...draft, content: "# Edited" }); }));
  const saveDistillation = vi.fn().mockResolvedValue({ ...draft, state: "saved" });
  installDocMindApi({ memory: { getDistillation: vi.fn().mockResolvedValue(draft), updateDistillation, saveDistillation } });
  render(<QueryClientProvider client={new QueryClient()}><DistillationEditor distillationId={draft.id} /></QueryClientProvider>);
  const user = userEvent.setup();
  const body = await screen.findByLabelText("蒸馏正文");
  await user.clear(body); await user.type(body, "# Edited"); await user.tab();
  await user.click(screen.getByRole("radio", { name: "仅本地" }));
  await user.click(screen.getByRole("button", { name: "保存知识" }));
  expect(saveDistillation).not.toHaveBeenCalled();
  finishUpdate();
  await vi.waitFor(() => expect(saveDistillation).toHaveBeenCalledWith(draft.id, { target: "local" }));
});
