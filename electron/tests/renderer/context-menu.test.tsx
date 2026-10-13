import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { ContextMenuTrigger } from "../../renderer/src/components/ContextMenu";

/**
 * 行操作菜单的三种入口与关闭方式。
 *
 * 这些用例存在的理由：把重命名/移除从平铺图标收进菜单之后，"够不够得着"这件事就全靠
 * 这份组件了 —— 纯右键对键盘不可达，所以「⋯」与 Shift+F10 必须是会红的能力，而不是
 * 写在注释里的承诺。
 */
function Harness({ onSelect = vi.fn() }: { onSelect?: () => void }) {
  const [count, setCount] = useState(0);
  return (
    <div>
      <button type="button">别处</button>
      <div className="tree-row">
        <ContextMenuTrigger
          items={[
            { label: "立即同步", onSelect: () => onSelect() },
            { label: "重命名 项目计划", text: "重命名", onSelect: () => onSelect() },
            {
              label: "移除 项目计划",
              text: "移除知识库…",
              danger: true,
              onSelect: () => onSelect(),
            },
          ]}
          label="知识库操作"
          triggerLabel="项目计划 的操作"
        >
          <span />
        </ContextMenuTrigger>
      </div>
      <button onClick={() => setCount((value) => value + 1)} type="button">
        计数 {count}
      </button>
    </div>
  );
}

const trigger = () => screen.getByRole("button", { name: "项目计划 的操作" });

beforeEach(() => {
  // jsdom 不给元素任何尺寸，弹窗定位会全落在 0 上；给每个元素一个方框，
  // "贴着右边缘时向左翻转"这类行为才有意义。元素自己的 rect 值按需设置。
  Element.prototype.getBoundingClientRect = function getBoundingClientRect(this: Element) {
    const name = this.getAttribute("aria-label") ?? this.textContent ?? "";
    return {
      x: 100,
      y: 100,
      top: 100,
      left: 100,
      right: 400,
      bottom: 130,
      width: 300,
      height: 30,
      toJSON: () => ({}),
      ...(name.startsWith("立即同步") ? { right: 1800, bottom: 1500 } : {}),
    } as DOMRect;
  };
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("行操作菜单", () => {
  it("点「⋯」打开菜单并列出操作", async () => {
    render(<Harness />);

    fireEvent.click(trigger());

    expect(await screen.findByRole("menu", { name: "知识库操作" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "立即同步" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "重命名 项目计划" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "移除 项目计划" })).toBeVisible();
    // 无障碍名是带名字的，看到的是短文案 —— 两者都在，读屏才分得清是哪一行的菜单。
    expect(screen.getByText("移除知识库…")).toBeVisible();
  });

  it("右键行内任意位置也能打开", async () => {
    render(<Harness />);

    fireEvent.contextMenu(trigger(), { clientX: 120, clientY: 130 });

    expect(await screen.findByRole("menu", { name: "知识库操作" })).toBeVisible();
  });

  it("Shift+F10 打开：键盘用户够得着这些操作", async () => {
    render(<Harness />);

    trigger().focus();
    fireEvent.keyDown(trigger(), { key: "F10", shiftKey: true });

    expect(await screen.findByRole("menu", { name: "知识库操作" })).toBeVisible();
  });

  it("打开后焦点落在第一个可用项，上下键在项之间走", async () => {
    render(<Harness />);

    fireEvent.click(trigger());

    const first = await screen.findByRole("menuitem", { name: "立即同步" });
    await waitFor(() => expect(first).toHaveFocus());
    // 每次都在**当前**焦点元素上按键：焦点已经移走之后再往旧元素上发 keydown，
    // 是在测一个用户到不了的状态。
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "重命名 项目计划" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "移除 项目计划" })).toHaveFocus();
    // 绕圈：最后一项再按向下回到第一项，而不是卡在末尾。
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "立即同步" })).toHaveFocus();
  });

  it("跳过的禁用项不接收焦点", async () => {
    render(
      <ContextMenuTrigger
        items={[
          { label: "立即同步", disabled: true, onSelect: vi.fn() },
          { label: "重命名 项目计划", text: "重命名", onSelect: vi.fn() },
        ]}
        label="知识库操作"
        triggerLabel="项目计划 的操作"
      >
        <span />
      </ContextMenuTrigger>,
    );

    fireEvent.click(trigger());

    const rename = await screen.findByRole("menuitem", { name: "重命名 项目计划" });
    await waitFor(() => expect(rename).toHaveFocus());
    fireEvent.keyDown(rename, { key: "ArrowDown" });
    expect(rename).toHaveFocus();
  });

  it("点菜单项先关菜单再执行", async () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);

    fireEvent.click(trigger());
    fireEvent.click(await screen.findByRole("menuitem", { name: "重命名 项目计划" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu", { name: "知识库操作" })).toBeNull();
  });

  it("Esc 关闭并把焦点还回触发按钮", async () => {
    render(<Harness />);

    fireEvent.click(trigger());
    await screen.findByRole("menu", { name: "知识库操作" });
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("menu", { name: "知识库操作" })).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it("点别处关闭，且不会顺手点到那一处", async () => {
    render(<Harness />);

    fireEvent.click(trigger());
    await screen.findByRole("menu", { name: "知识库操作" });
    fireEvent.mouseDown(document.body);

    expect(screen.queryByRole("menu", { name: "知识库操作" })).toBeNull();
    expect(screen.getByRole("button", { name: "计数 0" })).toBeVisible();
  });

  it("滚动列表就关掉：菜单锚在行上，位置一旦对不上就该消失", async () => {
    render(<Harness />);

    fireEvent.click(trigger());
    await screen.findByRole("menu", { name: "知识库操作" });
    fireEvent.scroll(window);

    expect(screen.queryByRole("menu", { name: "知识库操作" })).toBeNull();
  });

  it("贴着视口边缘时向内翻转，菜单不会跑到屏幕外", async () => {
    render(<Harness />);
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 });

    // 触发按钮贴着右下角：菜单要向左、向上收，而不是伸出视口。
    fireEvent.click(trigger());
    const menu = await screen.findByRole("menu", { name: "知识库操作" });
    const placement = menu.getBoundingClientRect();

    expect(placement.left).toBeGreaterThanOrEqual(0);
    expect(placement.top).toBeGreaterThanOrEqual(0);
  });
});
