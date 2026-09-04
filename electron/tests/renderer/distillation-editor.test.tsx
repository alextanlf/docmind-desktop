import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DistillationEditor } from "../../renderer/src/features/memory/DistillationEditor";
import { installDocMindApi, repository } from "./test-docmind-api";

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
