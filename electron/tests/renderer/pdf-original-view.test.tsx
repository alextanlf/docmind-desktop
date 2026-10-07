import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { DocumentEditor } from "../../renderer/src/features/repositories/DocumentEditor";
import { document, documentWithPdf, installDocMindApi } from "./test-docmind-api";

const hoisted = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getPage: vi.fn(),
  destroy: vi.fn(),
}));

vi.mock("../../renderer/src/features/repositories/pdf-runtime", async (importOriginal) => {
  // 只替换 getDocument：PDFDataRangeTransport 用真类，
  // 否则传输层的真实行为会被 mock 掉。
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, getDocument: hoisted.getDocument };
});

function renderEditor(target: Parameters<typeof DocumentEditor>[0]["document"]) {
  return render(
    <AppProviders>
      <DocumentEditor document={target} onClose={() => undefined} />
    </AppProviders>,
  );
}

describe("文档原件视图", () => {
  beforeEach(() => {
    appQueryClient.clear();
    hoisted.getDocument.mockReset();
    hoisted.getPage.mockReset();
    hoisted.destroy.mockReset().mockResolvedValue(undefined);
    hoisted.getPage.mockResolvedValue({
      getViewport: () => ({ width: 600, height: 800 }),
      render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
      cleanup: vi.fn(),
    });
    hoisted.getDocument.mockReturnValue({
      promise: Promise.resolve({
        numPages: 2,
        getPage: hoisted.getPage,
        destroy: hoisted.destroy,
      }),
      destroy: hoisted.destroy,
    });
  });

  it("opens PDF-backed documents in the original view, with both sources reachable", async () => {
    const readOriginalChunk = vi.fn().mockResolvedValue({ data: "AQID", total: 2048 });
    installDocMindApi({ documents: { readOriginalChunk } });

    renderEditor(documentWithPdf);

    const group = screen.getByRole("group", { name: "内容来源" });
    expect(within(group).getByRole("button", { name: "原文" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // 阅读器起来就说明总页数来自真实 PDF 句柄，而不是占位。
    await screen.findByRole("toolbar", { name: "原件阅读工具" });
    expect(screen.getByText("/ 2")).toBeInTheDocument();
    expect(screen.queryByLabelText("文档内容")).not.toBeInTheDocument();
    expect(readOriginalChunk).toHaveBeenCalledWith(documentWithPdf.id, 0, 2047);
  });

  it("switches to the parsed Markdown on demand", async () => {
    installDocMindApi({
      documents: { readOriginalChunk: vi.fn().mockResolvedValue({ data: "AQID", total: 2048 }) },
    });

    renderEditor(documentWithPdf);
    await screen.findByRole("toolbar", { name: "原件阅读工具" });

    fireEvent.click(screen.getByRole("button", { name: "解析" }));

    const view = screen.getByLabelText("文档内容");
    expect(view.querySelector("h1")).toHaveTextContent("State 管理");
    expect(screen.queryByRole("toolbar", { name: "原件阅读工具" })).not.toBeInTheDocument();
  });

  it("offers no source toggle when the document has no PDF original", () => {
    installDocMindApi();

    renderEditor(document);

    expect(screen.queryByRole("group", { name: "内容来源" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("文档内容")).toBeInTheDocument();
    // 没有原件就不该去碰后端。
    expect(hoisted.getDocument).not.toHaveBeenCalled();
  });

  it("treats a zero-byte original as unavailable", () => {
    installDocMindApi();

    renderEditor({ ...documentWithPdf, originalByteSize: 0 });

    expect(screen.queryByRole("group", { name: "内容来源" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("文档内容")).toBeInTheDocument();
  });

  it("hides the source toggle while editing the parsed Markdown", async () => {
    installDocMindApi({
      documents: { readOriginalChunk: vi.fn().mockResolvedValue({ data: "AQID", total: 2048 }) },
    });

    renderEditor(documentWithPdf);
    await screen.findByRole("toolbar", { name: "原件阅读工具" });

    fireEvent.click(screen.getByRole("button", { name: "编辑文档" }));

    expect(screen.queryByRole("group", { name: "内容来源" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Markdown 内容")).toBeInTheDocument();
  });

  it("reports a failed original instead of leaving the viewer blank", async () => {
    installDocMindApi({
      documents: { readOriginalChunk: vi.fn().mockRejectedValue(new Error("范围请求失败")) },
    });

    const { container } = renderEditor(documentWithPdf);

    // 显示的文案走 clientErrorMessage，非协议错误会被收敛成通用提示，
    // 所以断言落在「有错误态」而不是原始消息文本上。
    await waitFor(() => expect(container.querySelector(".pdf-original-error")).not.toBeNull());
    expect(screen.queryByRole("toolbar", { name: "原件阅读工具" })).not.toBeInTheDocument();
    expect(hoisted.getDocument).not.toHaveBeenCalled();
  });
});
