import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArchivedDocument, RemovedDocument } from "../../shared/contracts";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { ArchivedDocumentsSettings } from "../../renderer/src/features/settings/ArchivedDocumentsSettings";
import { installDocMindApi, repository } from "./test-docmind-api";

const archived: ArchivedDocument = {
  id: "00000000-0000-4000-8000-000000000501",
  repositoryId: repository.id,
  repositoryName: repository.name,
  title: "归档的甲",
  remoteId: "remote-1",
  archivedAt: "2026-08-31T10:00:00Z",
};

const localArchived: ArchivedDocument = {
  ...archived,
  id: "00000000-0000-4000-8000-000000000502",
  title: "归档的乙",
  remoteId: null,
};

const legacyRemoved: RemovedDocument = {
  id: "00000000-0000-4000-8000-000000000601",
  repositoryId: repository.id,
  remoteId: "remote-9",
  title: "旧移除的丙",
  repositoryName: repository.name,
  removedAt: "2026-08-31T09:00:00Z",
};

function renderSettings() {
  return render(
    <AppProviders>
      <ArchivedDocumentsSettings />
    </AppProviders>,
  );
}

describe("已归档的文档", () => {
  beforeEach(() => appQueryClient.clear());

  it("says so plainly when nothing has ever been archived", async () => {
    installDocMindApi();
    renderSettings();

    expect(await screen.findByText(/还没有归档的文档/)).toBeVisible();
  });

  it("restores an archived document without downloading anything again", async () => {
    // 归档只写了一个时间戳，内容与向量都还在 —— 所以恢复就是取消归档，
    // 不该触发同步（那会把整篇重新下载并重新索引一遍）。
    const api = installDocMindApi({
      documents: { listArchived: vi.fn().mockResolvedValue([archived]) },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: `恢复 ${archived.title}` }));

    await waitFor(() => expect(api.documents.unarchive).toHaveBeenCalledWith(archived.id));
    expect(api.sync.trigger).not.toHaveBeenCalled();
  });

  it("keeps the type-the-title gate for the permanent delete, and only for it", async () => {
    const api = installDocMindApi({
      documents: { listArchived: vi.fn().mockResolvedValue([localArchived]) },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: `永久删除 ${localArchived.title}` }));

    const confirm = screen.getByRole("button", { name: "永久删除" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("输入文档标题以确认"), {
      target: { value: localArchived.title },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(api.documents.remove).toHaveBeenCalledWith(localArchived.id, true));
    expect(api.documents.deleteRemote).not.toHaveBeenCalled();
  });

  it("deletes the remote original only after an explicit opt-in", async () => {
    const api = installDocMindApi({
      documents: { listArchived: vi.fn().mockResolvedValue([archived]) },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: `永久删除 ${archived.title}` }));
    fireEvent.click(screen.getByLabelText("同时删除远端原文（不可撤销）"));
    // 勾上之后必须出现不可撤销的警示，否则用户是"盲勾"。
    expect(screen.getByText(/远端原文将被永久删除/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("输入文档标题以确认"), {
      target: { value: archived.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "连远端一起删除" }));

    await waitFor(() => expect(api.documents.deleteRemote).toHaveBeenCalledWith(archived.id, true));
    expect(api.documents.remove).not.toHaveBeenCalled();
  });

  it("restores a legacy removed document and then syncs that repository", async () => {
    // 旧流程留下的记录里文档行已经被删掉了：只删记录不会让内容回来，必须紧接着同步。
    const api = installDocMindApi({
      documents: { listRemoved: vi.fn().mockResolvedValue([legacyRemoved]) },
    });
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: `恢复 ${legacyRemoved.title}` }));

    await waitFor(() =>
      expect(api.documents.restoreRemoved).toHaveBeenCalledWith(
        legacyRemoved.repositoryId,
        legacyRemoved.id,
      ),
    );
    await waitFor(() => expect(api.sync.trigger).toHaveBeenCalledWith(legacyRemoved.repositoryId));
  });
});
