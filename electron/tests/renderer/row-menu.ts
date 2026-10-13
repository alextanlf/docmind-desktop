import { fireEvent, screen } from "@testing-library/react";

/**
 * 打开某一行的操作菜单并点其中一项。
 *
 * 侧边栏的操作（重命名、移除、立即同步）不再平铺成图标，而在行菜单里 —— 右键、「⋯」、
 * Shift+F10 三种入口共用同一份。测试走「⋯」这条路：右键在 jsdom 里要合成 contextmenu
 * 事件，而这条路径本身也要被覆盖（它是键盘用户唯一的入口）。
 *
 * `rowName` 是那一行的名字（知识库名或文档标题），`itemName` 是菜单项的无障碍名。
 */
export async function openRowMenu(rowName: string, itemName: string | RegExp) {
  fireEvent.click(await screen.findByRole("button", { name: `${rowName} 的操作` }));
  fireEvent.click(await screen.findByRole("menuitem", { name: itemName }));
}
