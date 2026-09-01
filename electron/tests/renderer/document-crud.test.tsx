import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { DeleteDocumentDialog } from "../../renderer/src/features/repositories/DeleteDocumentDialog";
import { DocumentEditor } from "../../renderer/src/features/repositories/DocumentEditor";
import { document, installDocMindApi, repository } from "./test-docmind-api";

describe("文档编辑", () => {
  beforeEach(() => appQueryClient.clear());

  it("keeps document deletion disabled until the exact visible title is entered", () => {
    installDocMindApi();
    render(
      <DeleteDocumentDialog
        document={document}
        repositoryName={repository.name}
        onClose={() => undefined}
      />,
    );

    const remove = screen.getByRole("button", { name: "删除文档" });
    expect(remove).toBeDisabled();
    fireEvent.change(screen.getByLabelText("输入文档标题以确认"), {
      target: { value: document.title },
    });
    expect(remove).toBeEnabled();
  });

  it("saves Markdown edits and refreshes the repository documents", async () => {
    const api = installDocMindApi();
    render(
      <AppProviders>
        <DocumentEditor document={document} onClose={() => undefined} />
      </AppProviders>,
    );

    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "更新后的 State" } });
    fireEvent.change(screen.getByLabelText("Markdown 内容"), { target: { value: "# 新内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文档" }));

    await waitFor(() =>
      expect(api.documents.update).toHaveBeenCalledWith(document.id, {
        title: "更新后的 State",
        content: "# 新内容",
      }),
    );
  });
});
