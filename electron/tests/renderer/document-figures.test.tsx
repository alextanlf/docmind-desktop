import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentFigures } from "../../renderer/src/features/repositories/DocumentFigures";
import { installDocMindApi } from "./test-docmind-api";

/** 1×1 PNG。测试不关心图长什么样，只关心字节有没有变成可渲染的 src。 */
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const DOCUMENT_ID = "00000000-0000-0000-0000-000000000022";

function figure(overrides: Partial<Parameters<typeof DocumentFigures>[0]["figures"][number]> = {}) {
  return {
    asset: `${"a".repeat(64)}.png`,
    pageNumber: 12,
    caption: "Voltage decay over time",
    width: 240,
    height: 160,
    bbox: [10, 20, 130, 100] as [number, number, number, number],
    fullPage: false,
    ...overrides,
  };
}

describe("文档插图", () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let originalCreate: typeof URL.createObjectURL;
  let originalRevoke: typeof URL.revokeObjectURL;

  beforeEach(() => {
    createObjectURL = vi.fn().mockReturnValue("blob:figure");
    revokeObjectURL = vi.fn();
    // jsdom 没有实现 createObjectURL；注入替身而不是让组件退回 data: URL，
    // 那样测不到「字节是否真的走了 IPC」。
    //
    // 直接挂静态属性，不能用 `vi.stubGlobal("URL", {...URL})`：URL 是类，静态方法在
    // 构造函数上，展开语法拷不过来，于是 stub 出来的对象两个方法都是 undefined。
    originalCreate = URL.createObjectURL;
    originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
  });

  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it("没有插图时不渲染任何东西", () => {
    installDocMindApi();
    const { container } = render(<DocumentFigures documentId={DOCUMENT_ID} figures={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("经 IPC 取图并渲染，标注里带页码与图注", async () => {
    const readAsset = vi.fn().mockResolvedValue(PNG_BASE64);
    installDocMindApi({ documents: { readAsset } });

    render(<DocumentFigures documentId={DOCUMENT_ID} figures={[figure()]} />);

    const image = await screen.findByRole("img");
    expect(image).toHaveAttribute("src", "blob:figure");
    // 页码与图注必须**同时**出现：扫描课件里同一张图会在多页反复出现，只有图注就不知道
    // 当前看的是哪一页那一张，也没法去原件对照。
    expect(screen.getByText("第 12 页插图")).toBeInTheDocument();
    expect(screen.getByText("Voltage decay over time")).toBeInTheDocument();
    expect(readAsset).toHaveBeenCalledWith(DOCUMENT_ID, `${"a".repeat(64)}.png`);
  });

  it("图注为空时退回按页描述，而不是留空", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });

    render(<DocumentFigures documentId={DOCUMENT_ID} figures={[figure({ caption: "" })]} />);

    await screen.findByRole("img");
    expect(screen.getByText("第 12 页插图")).toBeInTheDocument();
  });

  it("整页扫描图标注为整页，与页内插图区分开", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });

    render(
      <DocumentFigures
        documentId={DOCUMENT_ID}
        figures={[figure({ caption: "", fullPage: true })]}
      />,
    );

    await screen.findByRole("img");
    expect(screen.getByText("第 12 页（整页扫描）")).toBeInTheDocument();
  });

  it("单张图取不到时只影响那一张，其余照常显示", async () => {
    // 第一张失败、第二张成功 —— 断言失败之后循环没有中断。
    const readAsset = vi
      .fn()
      .mockRejectedValueOnce(new Error("missing"))
      .mockResolvedValue(PNG_BASE64);
    installDocMindApi({ documents: { readAsset } });

    const second = figure({ asset: `${"b".repeat(64)}.png`, pageNumber: 13, caption: "Second" });
    render(<DocumentFigures documentId={DOCUMENT_ID} figures={[figure(), second]} />);

    // 失败的那张仍然显示页码，用户至少知道它存在、该去第几页对照。
    const placeholder = await screen.findByText(/这张图暂时打不开/);
    expect(placeholder).toHaveTextContent("第 12 页");
    await waitFor(() => {
      expect(screen.getByAltText("Second")).toHaveAttribute("src", "blob:figure");
    });
  });

  it("卸载时释放 object URL，避免扫描件的图一直占着内存", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });

    const { unmount } = render(<DocumentFigures documentId={DOCUMENT_ID} figures={[figure()]} />);
    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalled();
    });

    unmount();

    expect(revokeObjectURL).toHaveBeenCalledWith("blob:figure");
  });
});

describe("点图定位到原件", () => {
  function locatable(
    overrides: Partial<Parameters<typeof DocumentFigures>[0]["figures"][number]> = {},
  ) {
    return figure({ pageNumber: 12, bbox: [72, 96, 372, 296], ...overrides });
  }

  it("点图把页码与版面位置交给调用方", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });
    const onLocate = vi.fn();

    render(
      <DocumentFigures documentId={DOCUMENT_ID} figures={[locatable()]} onLocate={onLocate} />,
    );

    const image = await screen.findByRole("img");
    fireEvent.click(image);

    // bbox 是「第 12 页的哪个位置」——只给页码无法确认是同一页上的哪一张图。
    expect(onLocate).toHaveBeenCalledWith({ page: 12, bbox: [72, 96, 372, 296] });
  });

  it("整页扫描图只跳页，bbox 交给调用方的是 null", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });
    const onLocate = vi.fn();

    render(
      <DocumentFigures
        documentId={DOCUMENT_ID}
        figures={[locatable({ bbox: null, fullPage: true })]}
        onLocate={onLocate}
      />,
    );

    fireEvent.click(await screen.findByRole("img"));

    // 整页扫描件的 bbox 就是整个页面，框出来等于没框 —— 明确给 null，别让它去画。
    expect(onLocate).toHaveBeenCalledWith({ page: 12, bbox: null });
  });

  it("没有可定位原件时图不可点，而不是点了没反应", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });

    render(<DocumentFigures documentId={DOCUMENT_ID} figures={[locatable()]} />);

    const image = await screen.findByRole("img");
    // 不传回调 = 这篇文档没得跳（Word/HTML 的图没有页面几何，远程文档没有本地 PDF）。
    expect(image.closest("button")).toBeNull();
    expect(screen.queryByRole("button", { name: /原件/ })).not.toBeInTheDocument();
  });

  it("显式按钮与点图走同一条路径", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });
    const onLocate = vi.fn();

    render(
      <DocumentFigures documentId={DOCUMENT_ID} figures={[locatable()]} onLocate={onLocate} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "在原件中定位" }));

    expect(onLocate).toHaveBeenCalledWith({ page: 12, bbox: [72, 96, 372, 296] });
  });

  it("没有页码的图不给定位入口", async () => {
    installDocMindApi({
      documents: { readAsset: vi.fn().mockResolvedValue(PNG_BASE64) },
    });
    const onLocate = vi.fn();

    render(
      <DocumentFigures
        documentId={DOCUMENT_ID}
        figures={[locatable({ pageNumber: null })]}
        onLocate={onLocate}
      />,
    );

    const image = await screen.findByRole("img");
    expect(image.closest("button")).toBeNull();
  });
});
