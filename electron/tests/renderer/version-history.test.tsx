import { render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { VersionHistory } from "../../renderer/src/features/repositories/VersionHistory";
import { installDocMindApi } from "./test-docmind-api";

describe("version history", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("lists document versions", async () => {
    installDocMindApi({
      versions: {
        list: vi.fn().mockResolvedValue([
          {
            documentId: "00000000-0000-0000-0000-000000000022",
            versionNo: 2,
            title: "State 管理",
            content: "# State 管理\n\nv2",
            contentSha256: "abc",
            createdAt: "2026-09-08T00:00:00Z",
          },
        ]),
      },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <VersionHistory documentId="00000000-0000-0000-0000-000000000022" />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("版本 2")).toBeVisible();
  });
});
