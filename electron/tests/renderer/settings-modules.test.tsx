/**
 * The settings module registry, tested on its own.
 *
 * The point of the registry is that nothing enumerates modules by hand, so the
 * behaviour worth locking is what happens when a module appears that the view
 * has never heard of. That cannot be exercised through `SettingsView` — its
 * built-ins register once at import time — so these tests drive the registry
 * directly against a clean slate.
 */
import { Puzzle } from "lucide-react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  defaultSettingsModule,
  registerModule,
  resetModulesForTest,
  settingsModuleById,
  settingsModuleTitleId,
  settingsModules,
} from "../../renderer/src/features/settings/settings-modules";

function module(id: string, order: number, label = id) {
  return { id, label, icon: Puzzle, order, render: () => <p>{label}</p> };
}

describe("settings module registry", () => {
  // The registry is process-wide module state, so each test starts empty.
  // Without this, one test's fake module leaks into the next and the failure
  // reads as a wrong-id assertion in an unrelated case.
  beforeEach(() => resetModulesForTest());

  it("orders modules by their declared order, not by registration sequence", () => {
    registerModule(module("late-but-first", 1));
    registerModule(module("early-but-late", 99));
    registerModule(module("middle", 50));

    // Dirty input on purpose: registered 1, 99, 50 — so a registry that
    // returned insertion order fails this, which is exactly the bug a
    // "just append to the array" implementation has.
    expect(settingsModules().map((entry) => entry.id)).toEqual([
      "late-but-first",
      "middle",
      "early-but-late",
    ]);
  });

  it("breaks order ties by registration, so the sequence is stable", () => {
    registerModule(module("twin-a", 10));
    registerModule(module("twin-b", 10));
    registerModule(module("twin-c", 10));

    // Without a stable tiebreak `Array.prototype.sort` may return these in any
    // order, and the nav item under the user's cursor can move between renders.
    expect(settingsModules().map((entry) => entry.id)).toEqual([
      "twin-a",
      "twin-b",
      "twin-c",
    ]);
  });

  it("puts the lowest-ordered module first so a new page can become the landing one", () => {
    registerModule(module("zzz-last", 900));
    registerModule(module("aaa-first", 5));

    // What the view relies on instead of hard-coding an id: whatever module
    // declares the smallest order is what a user lands on.
    expect(defaultSettingsModule()?.id).toBe("aaa-first");
  });

  it("keeps a runtime-contributed module addressable like any built-in", () => {
    // The extensibility claim: a module nobody wrote a branch for is still
    // reachable by id, appears in the nav, and carries its own copy.
    registerModule(module("vendor-extra", 42, "第三方扩展"));

    const found = settingsModuleById("vendor-extra");

    expect(found?.label).toBe("第三方扩展");
    expect(settingsModules()).toContainEqual(found);
  });

  it("rejects a duplicate id instead of rendering two pages under one nav entry", () => {
    registerModule(module("dupe", 10));
    expect(() => registerModule(module("dupe", 20))).toThrow(/already registered/);
  });

  it("derives a title id from the module id so headings stay wired to labels", () => {
    expect(settingsModuleTitleId("settings-model")).toBe("settings-model-title");
  });

  it("returns undefined for an id nobody registered", () => {
    expect(settingsModuleById("no-such-module")).toBeUndefined();
    expect(defaultSettingsModule()).toBeUndefined();
  });
});
