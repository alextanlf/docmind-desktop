import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PAGE_HEIGHT,
  PAGE_WIDTH,
  ensurePdfReady,
  pageBox,
  type PdfFixture,
} from "./pdf-view-fixture";

/**
 * 「适合宽度」的回归用例。
 *
 * 这条 bug 的形态是"没反应"，根因不在算法而在观察者装没装上：量尺寸的 effect 只挂一次
 * （依赖 `[]`），而滚动容器是 pdf 加载完成后才渲染的 —— 挂载那一刻 ref 还是 null，直接
 * return，观察者再也不会被装上，可用尺寸永远是 0，页面停在 100%。真浏览器里实测到观察者
 * 回调数为 0。
 *
 * 所以这里断言的是**行为**：滚动容器出现之后，观察者必须已经在观察它，而且它报出来的
 * 尺寸真的要决定页面大小。只断言"渲染了工具栏"的用例在旧代码下照样绿。
 */
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

describe("PDF 适合宽度", () => {
  let fixture: PdfFixture;

  beforeEach(() => {
    fixture = ensurePdfReady({ getDocument });
    fixture.installApi();
  });

  afterEach(() => {
    fixture.cleanup();
  });

  it("滚动容器出现之后才装观察者，并按它报出的宽度决定页宽", async () => {
    // 必须在 render 之前换成"手动通知"的观察者：ensurePdfReady 装的那个在注册时就
    // 报一次尺寸，那样页面一开始就是 138%，"还没量到尺寸时停在 100%"这条就无从断言。
    const observers = fixture.useManualResizeObservers();

    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000024" />,
    );

    // 加载完成之前没有滚动容器，观察者也不该观察一个不存在的东西。
    expect(screen.queryByRole("button", { name: "适合宽度" })).toBeNull();
    await screen.findByLabelText("跳转到页");

    // 观察的必须是那个能代表工作区尺寸的盒子，不是被内容撑大的滚动容器。
    expect(observers.observed[0]).toHaveClass("pdf-original");
    // 量到的宽度还没进计算时，页面只能按 100% 铺；无论这一步跑到哪一帧，页宽都不能超过
    // 工作区 —— 这条不变量与"effect 有没有被冲刷"无关，断言时机因此不脆。
    expect(pageBox(view.container).width).toBeLessThanOrEqual(fixture.viewport.width);

    // 100% 时页宽 612 < 可用宽 900，"适合宽度"会算出 100% —— 看不出差别，所以先
    // 把工作区改窄再报一次，尺寸是否真的参与计算才看得出来。
    fixture.viewport.width = 500;
    act(() => observers.notify());

    await waitFor(() => {
      const { height, width } = pageBox(view.container);
      expect(width).toBeGreaterThan(0);
      expect(width).toBeLessThanOrEqual(fixture.viewport.width);
      // 高度由宽度推出，不受可用高度限制 —— 这正是与「适合页面」的分界。
      expect(height).toBeCloseTo((width / PAGE_WIDTH) * PAGE_HEIGHT, 3);
    });
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "适合页面" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("工作区变窄之后页面跟着变窄，而不是停在上一次的尺寸", async () => {
    const observers = fixture.useManualResizeObservers();
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000025" />,
    );
    await screen.findByLabelText("跳转到页");
    act(() => observers.notify());
    const before = pageBox(view.container).width;
    expect(before).toBeGreaterThan(0);

    fixture.viewport.width = 400;
    act(() => observers.notify());

    await waitFor(() => expect(pageBox(view.container).width).toBeLessThan(before));
  });
});
