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
      getViewport: () => ({
        width: 600,
        height: 800,
        // 替身按「乘以 scale」换算：真实的 pdf.js 会做这个变换（还处理 /Rotate 旋转）。
        // 用例断言的是**调用方把换算交给 viewport 了**，不是 pdf.js 的数学是否正确。
        convertToViewportRectangle: ([x0, y0, x1, y1]: number[]) => [
          x0 * 1,
          y0 * 1,
          x1 * 1,
          y1 * 1,
        ],
      }),
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

describe("从插图定位到原件", () => {
  const figureWithBox = {
    asset: `${"c".repeat(64)}.png`,
    pageNumber: 2,
    caption: "Figure 3",
    width: 300,
    height: 200,
    bbox: [72, 96, 372, 296] as [number, number, number, number],
    fullPage: false,
  };

  it("点图切到原件视图，并按 bbox 画出高亮框", async () => {
    installDocMindApi({
      documents: {
        readOriginalChunk: vi.fn().mockResolvedValue({ data: "AQID", total: 2048 }),
        readAsset: vi
          .fn()
          .mockResolvedValue(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
          ),
      },
    });

    const { container } = renderEditor({ ...documentWithPdf, figures: [figureWithBox] });

    // 先切到解析视图，再点图 —— 模拟用户真实路径。
    fireEvent.click(
      within(screen.getByRole("group", { name: "内容来源" })).getByRole("button", { name: "解析" }),
    );
    fireEvent.click(await screen.findByRole("img"));

    await waitFor(() => {
      expect(
        within(screen.getByRole("group", { name: "内容来源" })).getByRole("button", {
          name: "原文",
        }),
      ).toHaveAttribute("aria-pressed", "true");
    });
    // 高亮框按 bbox 的四条边定位：换算交给 viewport，这里断言结果落到了页面上。
    await waitFor(() => {
      const highlight = container.querySelector(".pdf-page-highlight");
      expect(highlight).not.toBeNull();
      expect(highlight).toHaveStyle({ left: "72px", top: "96px", width: "300px", height: "200px" });
    });
  });

  it("整页扫描图不画高亮框，只跳页", async () => {
    installDocMindApi({
      documents: {
        readOriginalChunk: vi.fn().mockResolvedValue({ data: "AQID", total: 2048 }),
        readAsset: vi
          .fn()
          .mockResolvedValue(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
          ),
      },
    });

    const { container } = renderEditor({
      ...documentWithPdf,
      figures: [{ ...figureWithBox, bbox: null, fullPage: true }],
    });

    fireEvent.click(
      within(screen.getByRole("group", { name: "内容来源" })).getByRole("button", { name: "解析" }),
    );
    fireEvent.click(await screen.findByRole("img"));

    await waitFor(() => {
      expect(
        within(screen.getByRole("group", { name: "内容来源" })).getByRole("button", {
          name: "原文",
        }),
      ).toHaveAttribute("aria-pressed", "true");
    });
    // 整页扫描件的 bbox 就是整个页面，画出来等于没画。
    expect(container.querySelector(".pdf-page-highlight")).toBeNull();
  });
});
