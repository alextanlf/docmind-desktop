import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { SessionList } from "../../renderer/src/features/chat/SessionList";
import { installDocMindApi, repository, session } from "./test-docmind-api";

describe("会话列表", () => {
  beforeEach(() => appQueryClient.clear());

  it("creates a session using the selected repository scope", async () => {
    const api = installDocMindApi();
    render(
      <AppProviders>
        <SessionList selectedRepositoryIds={[repository.id]} />
      </AppProviders>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "新建会话" }));

    await waitFor(() =>
      expect(api.chat.createSession).toHaveBeenCalledWith({ repositoryIds: [repository.id] }),
    );
    expect(await screen.findByRole("button", { name: session.title })).toBeVisible();
  });
});
