import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryTree } from "../../renderer/src/features/repositories/RepositoryTree";
import { installDocMindApi, repository } from "./test-docmind-api";

describe("知识库树", () => {
  beforeEach(() => appQueryClient.clear());

  it("only reads a repository's documents after the user expands it", async () => {
    const api = installDocMindApi();
    render(
      <AppProviders>
        <RepositoryTree />
      </AppProviders>,
    );

    expect(api.documents.list).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: `展开 ${repository.name}` }));
    expect(await screen.findByText("State 管理")).toBeVisible();
    expect(api.documents.list).toHaveBeenCalledWith(repository.id);
  });
});
