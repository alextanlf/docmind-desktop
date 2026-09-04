import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryView } from "../../renderer/src/features/memory/MemoryView";
import { installDocMindApi, repository } from "./test-docmind-api";

describe("memory management", () => { it("filters memory by repository", async () => { const list = vi.fn().mockResolvedValue({ items: [], nextCursor: null }); installDocMindApi({ memory: { list } }); render(<QueryClientProvider client={new QueryClient()}><MemoryView /></QueryClientProvider>); const user = userEvent.setup(); await user.selectOptions(await screen.findByLabelText("记忆知识库"), repository.id); expect(list).toHaveBeenCalledWith({ repositoryIds: [repository.id], kind: undefined }); }); });
