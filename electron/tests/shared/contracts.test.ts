import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BatchProgressPayloadSchema, MODEL_PRESET_IDS } from "../../shared/contracts";

/**
 * The preset list is copied between three places that share no type link:
 *   1. this package: `MODEL_PRESET_IDS` in shared/contracts.ts
 *   2. the renderer form: `PRESETS` in ModelSettingsForm.tsx
 *   3. the backend:     `MODEL_PRESETS` in backend/app/schemas/settings.py
 * A new vendor must be added to all three. These assertions fail loudly when
 * only some of them are updated.
 */
const readRepoFile = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

describe("MODEL_PRESET_IDS", () => {
  it("matches the renderer form's PRESETS keys exactly", () => {
    const source = readRepoFile("../../renderer/src/features/settings/ModelSettingsForm.tsx");
    const block = source.slice(source.indexOf("const PRESETS = {"));
    const keys = [...block.slice(0, block.indexOf("} as const")).matchAll(/^ {2}([a-z_]+):/gm)].map(
      (match) => match[1],
    );
    expect(new Set(keys)).toEqual(new Set(MODEL_PRESET_IDS));
  });

  it("matches the backend MODEL_PRESETS keys exactly", () => {
    const source = readRepoFile("../../../backend/app/schemas/settings.py");
    const block = source.slice(
      source.indexOf("MODEL_PRESETS = {"),
      source.indexOf("MODEL_CATALOG"),
    );
    const keys = [...block.matchAll(/^ {4}"([a-z_]+)":/gm)].map((match) => match[1]);
    expect(new Set(keys)).toEqual(new Set(MODEL_PRESET_IDS));
  });

  it("keeps 'custom' last so the form renders it as the fallback option", () => {
    expect(MODEL_PRESET_IDS[MODEL_PRESET_IDS.length - 1]).toBe("custom");
  });
});

describe("BatchProgressPayloadSchema", () => {
  it("accepts the complete nested batch progress envelope", () => {
    expect(
      BatchProgressPayloadSchema.safeParse({
        progress: 50,
        state: "running",
        message: "处理中",
        stage: "running",
        counts: { total: 2, selected: 2, completed: 1, failed: 0, skipped: 0 },
        itemId: null,
        itemState: null,
      }).success,
    ).toBe(true);
  });

  it("rejects out-of-contract stages", () => {
    expect(
      BatchProgressPayloadSchema.safeParse({
        progress: 0,
        state: "running",
        message: "处理中",
        stage: "import",
        counts: { total: 0, selected: 0, completed: 0, failed: 0, skipped: 0 },
        itemId: null,
        itemState: null,
      }).success,
    ).toBe(false);
  });
});
