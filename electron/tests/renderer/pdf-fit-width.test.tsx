import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 「适合宽度」的回归用例。
 *
 * 这条 bug 的形态是"没反应"，根因不在算法而在观察者装没装上：量宽度的 effect 只挂一次
 * （依赖 `[]`），而滚动容器是 pdf 加载完成后才渲染的 —— 挂载那一刻 ref 还是 null，直接
 * return，观察者再也不会被装上，`available` 永远是 0，页面停在 100%。真浏览器里实测到
 * 观察者回调数为 0。
 *
 * 所以这里断言的是**行为**：滚动容器出现之后，观察者必须已经在观察它，而且量到的宽度
 * 要真的决定页面尺寸。只断言"渲染了工具栏"的用例在旧代码下照样绿。
 */
const FIXTURE_TEXT_WIDTH = 612;
const FIXTURE_TEXT_HEIGHT = 792;
/** 工作区给的可用宽度（父元素 clientWidth），比页面宽 —— 适合宽度要把页面缩到它以内。 */
const AVAILABLE = 500;

const getDocument = vi.fn();
vi.mock("../../renderer/src/features/repositories/pdf-runtime", () => ({
  getDocument: (...args: unknown[]) => getDocument(...args),
  // 首片字节走的是 pdf.js 的 transport，它要模拟 Worker/主线程通信；这里只关心
  // "加载完成后滚动容器才出现"，所以换成一个什么都不做的替身。
  PDFDataRangeTransport: class {
    constructor(
      readonly total: number,
      readonly initialData: Uint8Array,
    ) {}
    requestDataRange() {}
  },
}));

const { PdfOriginalView } =
  await import("../../renderer/src/features/repositories/PdfOriginalView");
const { installDocMindApi } = await import("./test-docmind-api");

describe("PDF 适合宽度", () => {
  beforeEach(() => {
    getDocument.mockReset();
    let resolveDocument: ((value: unknown) => void) | undefined;
    const loaded = new Promise((resolve) => {
      resolveDocument = resolve;
    });
    getDocument.mockReturnValue({ promise: loaded, destroy: vi.fn() });
    // pdf.js 文档对象只需要 getPage(1).getViewport({scale:1}) 这一条路径。
    resolveDocument?.({
      numPages: 2,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({
          width: FIXTURE_TEXT_WIDTH * scale,
          height: FIXTURE_TEXT_HEIGHT * scale,
        }),
      }),
    });
    // 滚动容器的父元素（.pdf-original）是测量对象：宽度由工作区决定，与页面无关。
    Object.defineProperty(Element.prototype, "clientWidth", {
      configurable: true,
      get(this: Element) {
        return this.classList.contains("pdf-original") ? AVAILABLE : 0;
      },
    });
  });

  it("滚动容器出现之后才装观察者，并按量到的宽度决定页宽", async () => {
    const observed: Element[] = [];
    const observe = vi.fn((element: Element) => {
      observed.push(element);
    });
    const disconnect = vi.fn();
    class TrackingResizeObserver {
      constructor(private readonly callback: () => void) {}
      observe = observe;
      unobserve() {}
      disconnect = disconnect;
    }
    vi.stubGlobal("ResizeObserver", TrackingResizeObserver);
    installDocMindApi();

    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000024" />,
    );

    // 加载完成之前没有滚动容器，观察者也不该观察一个不存在的东西。
    expect(screen.queryByRole("button", { name: "适合宽度" })).toBeNull();
    const scroller = await screen.findByLabelText("跳转到页");
    expect(scroller).toBeVisible();

    await waitFor(() => expect(observe).toHaveBeenCalled());
    // 观察的必须是那个能代表工作区宽度的盒子，不是被内容撑大的滚动容器。
    expect(observed[0]).toHaveClass("pdf-original");

    // 100% 时页宽 612 > 可用 500；适合宽度要把它缩到可用宽度以内。
    const wrapper = view.container.querySelector<HTMLElement>(".pdf-page");
    await waitFor(() => {
      const width = Number.parseFloat(wrapper?.style.width ?? "0");
      expect(width).toBeGreaterThan(0);
      expect(width).toBeLessThanOrEqual(AVAILABLE);
    });
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    vi.unstubAllGlobals();
  });
});
