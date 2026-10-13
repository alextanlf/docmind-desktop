import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss, { type Rule } from "postcss";
import { describe, expect, it } from "vitest";

const stylesheet = postcss.parse(
  readFileSync(resolve(__dirname, "../../renderer/src/styles.css"), "utf8"),
);

type DeclarationWinner = {
  important: boolean;
  specificity: number;
  value: string;
};

function mediaApplies(rule: Rule, viewportWidth: number): boolean {
  let parent = rule.parent as
    { type?: string; name?: string; params?: string; parent?: unknown } | undefined;

  while (parent) {
    if (parent.type === "atrule" && parent.name === "media") {
      const params = parent.params ?? "";
      const maxWidth = params.match(/max-width:\s*(\d+)px/);
      const minWidth = params.match(/min-width:\s*(\d+)px/);

      if (maxWidth && viewportWidth > Number(maxWidth[1])) return false;
      if (minWidth && viewportWidth < Number(minWidth[1])) return false;
    }
    parent = parent.parent as
      { type?: string; name?: string; params?: string; parent?: unknown } | undefined;
  }

  return true;
}

function selectorMatches(selector: string, classes: Set<string>): boolean {
  const selectorClasses = [...selector.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
  return selectorClasses.length > 0 && selectorClasses.every((name) => classes.has(name));
}

function stylesAt(viewportWidth: number, ...classes: string[]): Record<string, string> {
  const winners = new Map<string, DeclarationWinner>();
  const classSet = new Set(classes);

  stylesheet.walkRules((rule) => {
    if (!mediaApplies(rule, viewportWidth)) return;

    for (const selector of rule.selectors) {
      if (!selectorMatches(selector, classSet)) continue;

      const specificity = [...selector.matchAll(/\.([\w-]+)/g)].length;
      rule.walkDecls((declaration) => {
        const current = winners.get(declaration.prop);
        const wins =
          !current ||
          (declaration.important && !current.important) ||
          (declaration.important === current.important && specificity >= current.specificity);

        if (wins) {
          winners.set(declaration.prop, {
            important: declaration.important,
            specificity,
            value: declaration.value,
          });
        }
      });
    }
  });

  return Object.fromEntries([...winners].map(([property, winner]) => [property, winner.value]));
}

describe("responsive workspace styles", () => {
  it("shows the open reference drawer and hides it only in the closed state at 960px", () => {
    const open = stylesAt(960, "workspace-grid", "workspace-reference");
    const closed = stylesAt(960, "workspace-grid", "reference-is-closed", "workspace-reference");

    expect(open).toMatchObject({
      position: "fixed",
      top: "0",
      right: "0",
      bottom: "0",
      width: "320px",
    });
    expect(open.visibility ?? "visible").toBe("visible");
    expect(open.transform ?? "none").toBe("none");
    expect(closed.visibility).toBe("hidden");
    expect(closed.transform).toBe("translateX(100%)");
    expect(stylesAt(960, "workspace-grid")["grid-template-columns"]).toBe(
      "64px minmax(480px, 1fr) 0",
    );
  });

  it("keeps the desktop workspace in its three-column grid", () => {
    expect(stylesAt(1440, "workspace-grid")["grid-template-columns"]).toBe(
      "var(--sidebar-width) minmax(480px, 1fr) var(--reference-width)",
    );
  });

  it("drives both panel widths from custom properties the resizer writes", () => {
    const grid = stylesAt(1440, "workspace-grid");
    expect(grid["--sidebar-width"]).toBe("248px");
    expect(grid["--reference-width"]).toBe("320px");
    expect(stylesAt(1440, "workspace-grid", "sidebar-is-collapsed")["--sidebar-width"]).toBe(
      "64px",
    );
    // The collapsed rail is a fixed 64px: a remembered width must not leak back in.
    expect(stylesAt(1440, "workspace-sidebar", "is-collapsed")["width"]).toBe("64px");
  });

  it("removes the width transition while a panel is being dragged", () => {
    expect(stylesAt(1440, "workspace-grid", "is-resizing", "workspace-sidebar")["transition"]).toBe(
      "none",
    );
  });

  it("hides the resizers once the drawer and forced-rail breakpoints take over", () => {
    expect(stylesAt(1100, "panel-resizer")["display"]).toBe("none");
    expect(
      stylesAt(1440, "workspace-grid", "reference-is-closed", "panel-resizer-reference")["display"],
    ).toBe("none");
  });

  /**
   * 记忆页的三行是「页头 / 筛选 / 分栏」，最后一行吃剩下的高度。`.workspace-content`
   * 只是块级容器，不会替子元素撑高：少了 `height: 100%`，第三行的 `minmax(0, 1fr)`
   * 就没有可分配空间，分栏退化成 86px 的内容高 —— 纵向分隔线只剩一小截，两个空状态
   * 都浮在页顶（实测 1440x900 下 `.memory-layout` 只有 86px）。
   */
  it("stretches the memory split view to the workspace height", () => {
    expect(stylesAt(1440, "memory-view")["height"]).toBe("100%");
    expect(stylesAt(1440, "memory-view")["grid-template-rows"]).toBe("auto auto minmax(0, 1fr)");
    // 空状态靠内边距对齐两栏的首行：列表栏自己一点内边距都没有，`<p>` 默认的
    // 1em 外边距又比主区的 22px 少 10px，两个提示会一高一低、且左边那条贴着分栏线。
    expect(stylesAt(1440, "memory-layout", "memory-empty")["margin"]).toBe("0");
    expect(stylesAt(1440, "memory-layout", "memory-empty")["padding"]).toBe("22px 24px");
  });
});

/**
 * 样式表里的"最后一条被解析的规则"不等于"浏览器用的那条"：特异性更高的一条即使写在
 * 前面也会赢。这个 PDF 页码输入框就栽在这上面 —— 全局表单规则
 * `input:not([type="checkbox"]):not([type="radio"]), select` 的特异性是 (0,2,1)
 * （两个 :not() 各自把参数里的属性选择器算了进来），所以单类名 (0,1,0) 甚至
 * `input.pdf-page-input.pdf-page-input` 的 (0,2,2) 全都输给它，输入框被拉成整行宽、
 * 36px 高，"/ 20" 被挤到框沿上。
 *
 * 这里的解析只需要覆盖"元素 + 类 + 属性 + :not()"这几种选择器：本仓库的样式表没有别的
 * 形态会参与表单控件的层叠。解析不了的（伪元素、状态伪类）一律当作不匹配。
 */
type Target = { tag: string; id?: string; classes: string[]; attrs: Record<string, string> };

type Specificity = [number, number, number];

type Declaration = {
  property: string;
  value: string;
  important: boolean;
  specificity: Specificity;
};

const MEDIA_WIDTH = 1440;

function specificityOf(selector: string): Specificity {
  const withoutNot = selector.replace(/:not\([^)]*\)/g, "");
  const notArguments = [...selector.matchAll(/:not\(([^)]*)\)/g)].map((match) => match[1]);
  const counted = [withoutNot, ...notArguments].join(" ");
  return [
    (counted.match(/#[\w-]+/g) ?? []).length,
    (counted.match(/\.[\w-]+/g) ?? []).length + (counted.match(/\[[^\]]+\]/g) ?? []).length,
    (counted.match(/(?:^|[\s>+~])([a-zA-Z][\w-]*)/g) ?? []).length,
  ];
}

function compareSpecificity(a: Specificity, b: Specificity): number {
  for (let index = 0; index < 3; index += 1) if (a[index] !== b[index]) return a[index] - b[index];
  return 0;
}

function compoundTargetMatches(compound: string, target: Target): boolean {
  if (compound.includes("::")) return false;
  const notArguments = [...compound.matchAll(/:not\(([^)]*)\)/g)].map((match) => match[1]);
  if (notArguments.some((argument) => compoundTargetMatches(argument, target))) return false;
  const base = compound.replace(/:not\([^)]*\)/g, "");
  if (base.includes(":") && !/:not\(/.test(base)) return false;

  const tag = base.match(/^[a-zA-Z][\w-]*/);
  if (tag && target.tag !== tag[0].toLowerCase()) return false;
  const id = base.match(/#([\w-]+)/);
  if (id && target.id !== id[1]) return false;
  for (const [, name] of base.matchAll(/\.([\w-]+)/g))
    if (!target.classes.includes(name)) return false;
  for (const [, name, value] of base.matchAll(/\[([\w-]+)(?:[~|^$*]?=)?["']?([^\]"']*)["']?\]/g)) {
    if (!(name in target.attrs)) return false;
    if (value && target.attrs[name] !== value) return false;
  }
  return true;
}

function targetSelectorMatches(selector: string, target: Target): boolean {
  return selector.split(",").some((part) =>
    part
      .trim()
      .split(/\s+/)
      .every((compound) => compoundTargetMatches(compound, target)),
  );
}

/** 某个元素最终生效的声明：特异性高的赢，同特异性时后写的赢，!important 永远赢。 */
function winningDeclarations(target: Target): Record<string, string> {
  const winners = new Map<string, Declaration>();

  stylesheet.walkRules((rule) => {
    if (!mediaApplies(rule, MEDIA_WIDTH)) return;
    for (const selector of rule.selectors) {
      if (!targetSelectorMatches(selector, target)) continue;
      const specificity = specificityOf(selector);
      rule.walkDecls((declaration) => {
        const current = winners.get(declaration.prop);
        const wins =
          !current ||
          (declaration.important && !current.important) ||
          (declaration.important === current.important &&
            compareSpecificity(specificity, current.specificity) >= 0);
        if (wins) {
          winners.set(declaration.prop, {
            property: declaration.prop,
            value: declaration.value,
            important: declaration.important,
            specificity,
          });
        }
      });
    }
  });

  return Object.fromEntries([...winners].map(([property, winner]) => [property, winner.value]));
}

describe("PDF 页码控件的层叠", () => {
  const pageInput: Target = {
    tag: "input",
    classes: ["pdf-page-input"],
    attrs: { type: "number" },
  };

  it("页数输入框的尺寸不再被全局表单规则吃掉", () => {
    // 断言的是浏览器真正会用的值，而不是"样式表里存在某个声明" —— 后者在旧写法下也成立。
    expect(winningDeclarations(pageInput)).toMatchObject({
      width: "48px",
      height: "28px",
      "font-size": "12px",
    });
  });

  it("总页数不换行，与输入框同高", () => {
    // 带上 counter 这个祖先类：在真实的 DOM 里这条规则是后代选择器命中的。
    expect(
      winningDeclarations({
        tag: "span",
        classes: ["pdf-page-counter", "pdf-page-total"],
        attrs: {},
      }),
    ).toMatchObject({ "white-space": "nowrap" });
    expect(
      winningDeclarations({ tag: "span", classes: ["pdf-page-counter"], attrs: {} }),
    ).toMatchObject({ display: "inline-flex", "align-items": "center" });
  });
});
