import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { RepositoryScope } from "../../renderer/src/features/chat/RepositoryScope";
import { SessionList } from "../../renderer/src/features/chat/SessionList";
import { installDocMindApi, repository, session } from "./test-docmind-api";

function ScopeHarness() {
  const [selected, setSelected] = useState([repository.id, "stale-repository"]);
  return (
    <>
      <output>{selected.join(",")}</output>
      <RepositoryScope
        onChange={setSelected}
        repositories={[repository]}
        selectedRepositoryIds={selected}
      />
    </>
  );
}

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

  it("limits scope and session creation to repositories with indexed documents", async () => {
    const pending = {
      ...repository,
      id: "pending-repository",
      name: "等待索引",
      indexedDocumentCount: 0,
    };
    const failed = {
      ...repository,
      id: "failed-repository",
      name: "索引失败",
      indexedDocumentCount: 0,
    };
    render(
      <AppProviders>
        <>
          <RepositoryScope
            onChange={() => undefined}
            repositories={[repository, pending, failed]}
            selectedRepositoryIds={[]}
          />
          <SessionList
            repositories={[repository, pending, failed]}
            selectedRepositoryIds={[pending.id]}
          />
        </>
      </AppProviders>,
    );

    fireEvent.click(screen.getByTitle("选择知识库"));

    expect(screen.getByText(repository.name)).toBeVisible();
    expect(screen.queryByText(pending.name)).not.toBeInTheDocument();
    expect(screen.queryByText(failed.name)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建会话" })).toBeDisabled();
  });

  it("removes stale repository IDs from the active scope", async () => {
    render(<ScopeHarness />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(repository.id));
    expect(screen.getByRole("status")).not.toHaveTextContent("stale-repository");
  });

  it("groups sessions deterministically and hides session titles in the collapsed rail", async () => {
    const yesterday = {
      ...session,
      id: "session-yesterday",
      title: "昨天的问题",
      updatedAt: "2026-08-30T12:00:00Z",
    };
    const older = {
      ...session,
      id: "session-older",
      title: "更早的问题",
      updatedAt: "2026-08-20T12:00:00Z",
    };
    installDocMindApi({
      chat: { listSessions: vi.fn().mockResolvedValue([session, yesterday, older]) },
    });
    render(
      <AppProviders>
        <SessionList
          collapsed
          repositories={[repository]}
          selectedRepositoryIds={[repository.id]}
        />
      </AppProviders>,
    );

    await screen.findByRole("button", { name: "新建会话" });

    expect(screen.queryByText("今天")).not.toBeInTheDocument();
    expect(screen.queryByText("昨天")).not.toBeInTheDocument();
    expect(screen.queryByText(session.title)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("keeps older groups distinct across calendar years", async () => {
    const previousYear = new Date().getFullYear() - 1;
    const twoYearsAgo = previousYear - 1;
    const previous = {
      ...session,
      id: "session-previous-year",
      title: "去年的问题",
      updatedAt: `${previousYear}-08-20T12:00:00Z`,
    };
    const older = {
      ...session,
      id: "session-two-years-ago",
      title: "前年的问题",
      updatedAt: `${twoYearsAgo}-08-20T12:00:00Z`,
    };
    installDocMindApi({
      chat: { listSessions: vi.fn().mockResolvedValue([previous, older]) },
    });
    render(
      <AppProviders>
        <SessionList selectedRepositoryIds={[repository.id]} />
      </AppProviders>,
    );

    expect(await screen.findByText(`${previousYear}年8月20日`)).toBeVisible();
    expect(screen.getByText(`${twoYearsAgo}年8月20日`)).toBeVisible();
  });
});
