import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ConflictList } from "../../renderer/src/features/repositories/ConflictList";
import { installDocMindApi } from "./test-docmind-api";

describe("conflict list", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("lists conflicts and resolves keep-local", async () => {
    const api = installDocMindApi({
      conflicts: {
        list: vi.fn().mockResolvedValue([
          {
            documentId: "00000000-0000-0000-0000-000000000022",
            title: "State 管理",
            localContent: "# Local",
            remoteContent: "# Remote",
          },
        ]),
      },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <ConflictList repositoryId="00000000-0000-0000-0000-000000000021" />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("State 管理")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保留本地" }));
    await waitFor(() =>
      expect(api.conflicts.resolve).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-000000000022",
        "keep_local",
      ),
    );
  });
});
