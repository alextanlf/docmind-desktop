/**
 * The settings module registry.
 *
 * The settings page used to be five `<section>` elements written out by hand in
 * `SettingsView.tsx` and a matching five-entry array in this file. That array
 * was the thing to fix: adding a settings page meant editing the view, and a
 * third-party plugin had no way to appear at all — the plugin catalogue was
 * dynamic, but the *page structure* around it was not.
 *
 * A module is now self-registering: it declares its own identity, its own
 * glyph, and how to render itself, and the page derives both the navigation and
 * the body from whatever is registered. Adding a built-in page is one
 * `registerModule` call. Letting a plugin contribute one is a data flag on its
 * manifest, not a renderer edit — see `useSettingsModules`.
 *
 * Two rules this file exists to enforce:
 *
 * - **The registry is the only list.** No page enumerates modules, orders them
 *   by hand, or knows which ones exist. `SettingsNav` and `SettingsView` both
 *   read `settingsModules()`, so they cannot disagree about what is on the page.
 * - **A module declares its own copy.** Labels and descriptions live with the
 *   module, never in the renderer, so a module contributed by a plugin can
 *   carry wording nobody here anticipated.
 */
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export interface SettingsModuleContext {
  /**
   * Rendered while the settings snapshot is still loading, and again if it
   * fails. Modules that need the settings payload read it from
   * `useSettingsQuery` themselves rather than receiving it as a prop, so a
   * module contributed at runtime needs no wiring here.
   */
  children: ReactNode;
}

export interface SettingsModule {
  /** Stable identity. Used as the DOM id, the nav key and the focus target. */
  id: string;
  /** Sidebar label. Declared by the module, never by the renderer. */
  label: string;
  /** One line under the page title. Optional: some pages speak for themselves. */
  description?: string;
  icon: LucideIcon;
  /**
   * Where in the order this module appears. Lower comes first. Modules that
   * tie are ordered by registration, so a plugin cannot silently displace a
   * built-in page.
   */
  order: number;
  render: () => ReactNode;
}

const modules: SettingsModule[] = [];

export function registerModule(module: SettingsModule): void {
  if (modules.some((existing) => existing.id === module.id)) {
    // Registration happens at import time, so a duplicate id is a programming
    // error, not a runtime condition to recover from. Failing loudly beats
    // rendering two pages under one nav entry.
    throw new Error(`settings module already registered: ${module.id}`);
  }
  modules.push(module);
}

/**
 * Drop every registered module. **Test use only.**
 *
 * The registry is append-only in the application: built-ins register on import
 * and live for the process lifetime. That is deliberate, but it makes the
 * registry untestable in isolation — one test registering a fake module leaks
 * it into every test that runs after it, and the failure surfaces as a
 * baffling wrong-id assertion in an unrelated case. This gives the registry
 * tests a clean slate without weakening the production contract.
 */
export function resetModulesForTest(): void {
  modules.length = 0;
}

/**
 * Every registered module, in display order.
 *
 * Sorted by `order` and then by registration sequence: a stable sort matters
 * because `SettingsNav` renders one button per module and a list that reshuffles
 * between renders would move the item under the user's cursor.
 */
export function settingsModules(): SettingsModule[] {
  return modules
    .map((module, index) => ({ module, index }))
    .sort((a, b) => a.module.order - b.module.order || a.index - b.index)
    .map((entry) => entry.module);
}

export function settingsModuleById(id: string): SettingsModule | undefined {
  return modules.find((module) => module.id === id);
}

/** The module shown when the page opens with nothing selected. */
export function defaultSettingsModule(): SettingsModule | undefined {
  return settingsModules()[0];
}

export function settingsModuleTitleId(id: string) {
  return `${id}-title`;
}
