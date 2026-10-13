import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ensurePdfReady, pageBox, type PdfFixture } from "./pdf-view-fixture";

/**
 * 缩放档位与手动输入的用例。
 *
 * 这里断言的是**最终落下来的缩放值**，不是"控件渲染出来了"：
 * - 选档位要真的改页宽，而不是只改下拉的显示；
 * - 手打百分比必须能被完整键入 —— 受控 number 输入边打字边收敛的话，
 *   想输入 150 会在敲下 "1" 的瞬间变成 50%，这条会在那里红；
 * - 越界与清空都必须回到一个可用的值，不能把 NaN / 0 送进页面尺寸。
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

describe("PDF 缩放档位", () => {
  let fixture: PdfFixture;

  beforeEach(() => {
    fixture = ensurePdfReady({ getDocument });
    fixture.installApi();
  });

  afterEach(() => {
    fixture.cleanup();
  });

  it("默认停在「适合宽度」，并把手算出来的百分比显示在输入框里", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000031" />,
    );

    const preset = await screen.findByLabelText("缩放档位");
    const input = screen.getByLabelText("缩放百分比");

    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));
    // 两个控件读的是同一个缩放值：下拉说"适合宽度"，输入框给出它等价于多少。
    expect(preset).toHaveValue("fit-width");
    // 900 宽的工作区里 (900 - 2×28) / 612 = 1.37908，页宽 612 × 1.37908 = 844；输入框
    // 显示的是这个比例的整数百分比 138%。两者是同一个缩放值的两种粒度，不是两个值。
    expect(input).toHaveValue(138);
    expect(pageBox(view.container).width).toBeCloseTo(844, 1);
  });

  it("选中预设档位就按该百分比铺页，并退出「适合宽度」", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000032" />,
    );
    const preset = await screen.findByLabelText("缩放档位");
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));

    fireEvent.change(preset, { target: { value: "125" } });

    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(765));
    expect(preset).toHaveValue("125");
    expect(screen.getByLabelText("缩放百分比")).toHaveValue(125);
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    // 回到档位下拉：页面尺寸必须重新由「适合宽度」决定（844），而不是停在 125% 的 765。
    fireEvent.change(preset, { target: { value: "fit-width" } });
    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(844, 1));
    expect(screen.getByRole("button", { name: "适合宽度" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("手打百分比能被完整键入，落地值再收敛到允许区间", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000033" />,
    );
    const input = await screen.findByLabelText("缩放百分比");
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));

    // 连续键入 150：中途每一次都不能被提前收敛，否则第二个字符会并进 50 里。
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.change(input, { target: { value: "15" } });
    fireEvent.change(input, { target: { value: "150" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(918));
    expect(screen.getByLabelText("缩放档位")).toHaveValue("150");

    // 超出上限的数字落到上限，而不是被丢弃或按原样铺页。
    fireEvent.change(input, { target: { value: "900" } });
    fireEvent.blur(input);
    await waitFor(() => expect(input).toHaveValue(400));

    // 不落在任何档位上的值，下拉如实说这是自定义值。
    fireEvent.change(input, { target: { value: "133" } });
    fireEvent.blur(input);
    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(813.96));
    expect(screen.getByLabelText("缩放档位")).toHaveValue("custom");
  });

  it("清空输入框不会把页面尺寸变成 NaN", async () => {
    const view = render(
      <PdfOriginalView byteSize={1024} documentId="00000000-0000-0000-0000-000000000034" />,
    );
    const input = await screen.findByLabelText("缩放百分比");
    await waitFor(() => expect(pageBox(view.container).width).toBeGreaterThan(0));
    const before = pageBox(view.container).width;

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    // 空值回到清空前的缩放，页面不变形。
    await waitFor(() => expect(pageBox(view.container).width).toBeCloseTo(before));
    expect(input).toHaveValue(138);
  });
});
