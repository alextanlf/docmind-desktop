import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { GraphView } from "../../renderer/src/features/graph/GraphView";
import { installDocMindApi } from "./test-docmind-api";

describe("graph view", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("lists nodes and adjacent relationships", async () => {
    installDocMindApi({
      graph: {
        nodes: vi
          .fn()
          .mockResolvedValue([{ id: "n1", kind: "citation", label: "S1", documentId: null }]),
        adjacency: vi
          .fn()
          .mockResolvedValue([{ sourceId: "doc-1", targetId: "n1", relation: "references" }]),
      },
    });

    render(
      <QueryClientProvider client={appQueryClient}>
        <GraphView />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("S1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "S1" }));
    expect(await screen.findByText("references")).toBeVisible();
  });
});
