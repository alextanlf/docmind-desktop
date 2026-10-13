import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PAGE_HEIGHT,
  PAGE_WIDTH,
  ensurePdfReady,
  pageBox,
  type PdfFixture,
} from "./pdf-view-fixture";

/**
 * 「适合页面」的用例。
 *
 * 与「适合宽度」的分界只有一条：**可用高度也参与算比例**。所以断言必须落在两侧都成立
 * 上 —— 只断言"页面变小了"的话，把高度那一项从 `Math.min` 里删掉照样绿（那时页面只是
 * 被宽度缩了一次）。这里用 900×700 的工作区配 612×792 的竖版页面：按宽适合是 138%，
 * 按高适合只有 80%，两个数字差得足够远，谁生效一目了然。
 */
const getDocument = vi.fn();

vi.mock("../../renderer/src/features/repositories/pdf-runtime", () => ({
  getDocument: (...args: unknown[]) => getDocument(...args),
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

describe("PDF 适合页面", () => {
  let fixture: PdfFixture;

  beforeEach(() => {
    fixture = ensurePdfReady({ getDocument });
    fixture.installApi();
  });

  afterEach(() => {
    fixture.cleanup();
  });

  it("整页缩进可视区：宽和高都放得下，且比只按宽度适合更小", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000041" />,
    );
    await screen.findByLabelText("跳转到页");
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));

    // 默认的「适合宽度」是 900×700 工作区里的 138%：下面这一条本身就断言了"只按宽度"
    // 会把页面撑得比可视区高 —— 适合页面要处理的正是这件事。
    const widthOnly = pageBox(view.container);
    expect(widthOnly.height).toBeGreaterThan(fixture.viewport.height);

    fireEvent.click(screen.getByRole("button", { name: "适合页面" }));

    await waitFor(() => {
      const box = pageBox(view.container);
      expect(box.height).toBeLessThanOrEqual(fixture.viewport.height);
      expect(box.width).toBeLessThanOrEqual(fixture.viewport.width);
      expect(box.width).toBeGreaterThan(0);
      // 比例是统一的：不是把某一维单独压扁。
      expect(box.height / box.width).toBeCloseTo(PAGE_HEIGHT / PAGE_WIDTH, 3);
    });

    const fitted = pageBox(view.container);
    expect(fitted.width).toBeLessThan(widthOnly.width);
    expect(screen.getByRole("button", { name: "适合页面" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    // 档位下拉与按钮报的是同一个状态。
    expect(screen.getByLabelText("缩放档位")).toHaveValue("fit-page");
  });

  it("工作区变高之后页面跟着变大：高度确实参与了计算", async () => {
    const observers = fixture.useManualResizeObservers();
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000042" />,
    );
    await screen.findByLabelText("跳转到页");
    act(() => observers.notify());
    fireEvent.click(screen.getByRole("button", { name: "适合页面" }));
    await waitFor(() => expect(pageBox(view.container).height).toBeGreaterThan(0));
    const before = pageBox(view.container);

    // 只给高度加空间：按宽度适合的比例不变，只有"高度这一项生效"才会让页面变大。
    fixture.viewport.height = 1100;
    act(() => observers.notify());

    await waitFor(() => expect(pageBox(view.container).height).toBeGreaterThan(before.height));
    expect(pageBox(view.container).height).toBeLessThanOrEqual(fixture.viewport.height);
  });

  it("两个「适合」按钮都是开关，再点一次交还手动缩放", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000043" />,
    );
    await screen.findByLabelText("跳转到页");
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));

    const fitPage = screen.getByRole("button", { name: "适合页面" });
    fireEvent.click(fitPage);
    await waitFor(() => expect(fitPage).toHaveAttribute("aria-pressed", "true"));

    // 再点一次：退出"适合"，比例交还给手动值（100%），而不是继续锁在整页尺寸上。
    fireEvent.click(fitPage);
    await waitFor(() => expect(pageBox(view.container).width).toBe(PAGE_WIDTH));
    expect(fitPage).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("缩放档位")).toHaveValue("100");

    // 换到另一个档位：两个按钮不能同时按下。
    fireEvent.click(screen.getByRole("button", { name: "适合宽度" }));
    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(844));
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(fitPage).toHaveAttribute("aria-pressed", "false");
  });

  it("手打百分比会自动退出「适合页面」", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000044" />,
    );
    await screen.findByLabelText("跳转到页");
    fireEvent.click(screen.getByRole("button", { name: "适合页面" }));
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));
    const fitted = pageBox(view.container).width;

    const input = screen.getByLabelText("缩放百分比");
    fireEvent.change(input, { target: { value: "100" } });
    fireEvent.keyDown(input, { key: "Enter" });

    // 100% 比"整页适合"更大：值真的落成了手动缩放，而不是被适合页面摁回去。
    await waitFor(() => expect(pageBox(view.container).width).toBe(PAGE_WIDTH));
    expect(pageBox(view.container).width).toBeGreaterThan(fitted);
    expect(screen.getByRole("button", { name: "适合页面" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByLabelText("缩放档位")).toHaveValue("100");
  });
});
