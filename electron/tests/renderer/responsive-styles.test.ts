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
      "248px minmax(480px, 1fr) 320px",
    );
  });
});
