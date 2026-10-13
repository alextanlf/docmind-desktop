import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  BatchProgressPayloadSchema,
  MODEL_PRESET_IDS,
  PluginDiagnosticSchema,
  SOURCE_KINDS,
  SourceRefSchema,
} from "../../shared/contracts";

/**
 * The preset list is copied between three places that share no type link:
 *   1. this package: `MODEL_PRESET_IDS` in shared/contracts.ts
 *   2. the renderer form: `PRESETS` in features/settings/model-presets.ts
 *   3. the backend:     `MODEL_PRESETS` in backend/app/schemas/settings.py
 * A new vendor must be added to all three. These assertions fail loudly when
 * only some of them are updated.
 */
const readRepoFile = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

const rendererPresetsSource = (): string =>
  readRepoFile("../../renderer/src/features/settings/model-presets.ts");

const backendPresetsSource = (): string => readRepoFile("../../../backend/app/schemas/settings.py");

/** 渲染层 `PRESETS` 里每个条目的 baseUrl / 默认 model。 */
const rendererPresetValues = (): Record<string, { baseUrl: string; model: string }> => {
  const source = rendererPresetsSource();
  const block = source.slice(
    source.indexOf("const PRESETS = {"),
    source.indexOf("} as const satisfies"),
  );
  const values: Record<string, { baseUrl: string; model: string }> = {};
  // 每个条目是 `key: { ... }`，花括号不嵌套，所以 [^}]* 足够。
  for (const [, key, body] of block.matchAll(/^ {2}([a-z_]+): \{([^}]*)\}/gms)) {
    const baseUrl = body.match(/baseUrl: "([^"]*)"/)?.[1];
    const model = body.match(/\bmodel: "([^"]*)"/)?.[1];
    if (baseUrl === undefined || model === undefined) {
      throw new Error(`PRESETS.${key} 解析失败：baseUrl=${baseUrl} model=${model}`);
    }
    values[key] = { baseUrl, model };
  }
  return values;
};

/** 后端 `MODEL_PRESETS` 里每个预设的 (baseUrl, 默认 model)。 */
const backendPresetValues = (): Record<string, { baseUrl: string; model: string }> => {
  const source = backendPresetsSource();
  const block = source.slice(source.indexOf("MODEL_PRESETS = {"), source.indexOf("MODEL_CATALOG"));
  const values: Record<string, { baseUrl: string; model: string }> = {};
  for (const [, key, baseUrl, model] of block.matchAll(
    /^ {4}"([a-z_]+)": \("([^"]*)", "([^"]*)"\)/gm,
  )) {
    values[key] = { baseUrl, model };
  }
  return values;
};

describe("MODEL_PRESET_IDS", () => {
  it("matches the renderer form's PRESETS keys exactly", () => {
    const source = rendererPresetsSource();
    const block = source.slice(source.indexOf("const PRESETS = {"));
    const keys = [...block.slice(0, block.indexOf("} as const")).matchAll(/^ {2}([a-z_]+):/gm)].map(
      (match) => match[1],
    );
    expect(new Set(keys)).toEqual(new Set(MODEL_PRESET_IDS));
  });

  it("matches the backend MODEL_PRESETS keys exactly", () => {
    const source = backendPresetsSource();
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

  /**
   * 键名对齐还不够：用户切到某个预设时，表单填的是**渲染层**这份 baseUrl 与
   * 默认 model，而「哪些模型可选」与能力档位来自**后端**。两边的值各改各的不会
   * 报任何错，只会让切换预设后表单里出现一个后端不认的模型 id，或把请求发到一个
   * 已被换掉的端点。键名相同会让人误以为这份表已经对齐了。
   */
  it("matches the backend MODEL_PRESETS base URLs and default models", () => {
    const renderer = rendererPresetValues();
    const backend = backendPresetValues();
    expect(Object.keys(renderer).sort()).toEqual([...MODEL_PRESET_IDS].sort());
    for (const preset of MODEL_PRESET_IDS) {
      expect(renderer[preset], `${preset} 的 baseUrl/model 与后端不一致`).toEqual(backend[preset]);
    }
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

/**
 * The plugin diagnostics record. The backend emits one record per plugin it
 * looked at, whether or not that plugin loaded, so the shape cannot depend on
 * the outcome — the common case in a working install is a plugin that loaded.
 */
describe("PluginDiagnosticSchema", () => {
  it("accepts a plugin that loaded, with an empty error", () => {
    // The regression this covers: `error` used to be required and absent for a
    // successful plugin, so the first working plugin would have failed the
    // client's parse and taken the whole panel down with it.
    expect(
      PluginDiagnosticSchema.safeParse({
        name: "acme",
        error: "",
        source: "/Users/someone/.docmind/plugins/acme",
      }).success,
    ).toBe(true);
  });

  it("keeps the two sides locked together", () => {
    // The backend normalises its records instead of letting the client special
    // case a missing key; a response in the old shape must be caught here rather
    // than rendering as a nameless failure. `source` is supplied so the only
    // thing this can fail on is the missing `error` — otherwise a second,
    // unrelated required field would mask the one under test.
    expect(PluginDiagnosticSchema.safeParse({ name: "acme", source: "/tmp/acme" }).success).toBe(
      false,
    );
  });

  it("carries the directory the plugin was found in", () => {
    const parsed = PluginDiagnosticSchema.parse({
      name: "acme",
      error: "boom",
      source: "/tmp/acme",
    });

    expect(parsed.source).toBe("/tmp/acme");
  });
});

/**
 * 导入来源引用。`name` 是这一组里唯一由客户端上报、又必须原样收回来的字段
 * （后端只把它当显示用），所以两边对「缺席」和「显式为 null」的理解必须一致。
 */
describe("SourceRefSchema", () => {
  it("accepts the explicit null the backend sends for a URL import", () => {
    // 后端 `SourceRef.name` 可空，且 FastAPI 不带 exclude_none：URL 导入与老记录的
    // 响应里就是 `"name": null`。只写 `.optional()` 会卡住它，导入进度接口整个被拒 ——
    // 而渲染层的假 API 根本不带这个键，vitest 抓不到，只有这条断言守着。
    expect(
      SourceRefSchema.safeParse({ kind: "staged_file", value: "0d17078a", name: null }).success,
    ).toBe(true);
  });

  it("still accepts a payload without the key at all", () => {
    expect(SourceRefSchema.safeParse({ kind: "url", value: "https://x.test/a" }).success).toBe(
      true,
    );
  });

  it("carries the file name the user picked through to the backend", () => {
    const parsed = SourceRefSchema.parse({
      kind: "staged_file",
      value: "0d17078a",
      name: "季度报告.pdf",
    });

    expect(parsed.name).toBe("季度报告.pdf");
  });
});

/**
 * 导入来源的类别跨语言对齐。
 *
 * 这条用例读的是后端源码本身（与 MODEL_PRESET_IDS 那组同一套做法）：两边各说各话的
 * 代价不是报错，而是运行时才炸的 ValidationError —— 加 `linked_file` 时后端就踩过一次，
 * 因为那份枚举当时在 `SourceRef` 与 `SourcePreview` 里各声明了一遍。
 */
describe("SOURCE_KINDS", () => {
  it("matches the backend's declared kinds exactly, in order", () => {
    const source = readRepoFile("../../../backend/app/schemas/imports.py");
    const declared = source.match(/^SOURCE_KINDS = \(([^)]*)\)/m);
    if (!declared) throw new Error("后端没有声明 SOURCE_KINDS");

    const backendKinds = [...declared[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);

    expect(backendKinds).toEqual([...SOURCE_KINDS]);
  });
});
