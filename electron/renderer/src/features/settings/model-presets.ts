import type { ModelPreset } from "../../../../shared/contracts";
import { DEFAULT_CUSTOM_LABEL, LEGACY_CUSTOM_PRESET } from "../../../../shared/contracts";

/**
 * 内置供应商的显示名、Base URL 与默认模型。
 *
 * 这张表被三处引用而彼此没有类型联系：`MODEL_PRESET_IDS`（shared/contracts）、
 * 这里、以及后端的 `MODEL_PRESETS`。新增一家厂商三处都要改，
 * `contracts.test.ts` 会逐键、逐值地比对，以免只改了一边。
 *
 * `custom` 是升级前唯一那家自定义厂商的字面量：它现在只是老配置的读取别名，
 * **不出现在下拉框里**（自定义供应商改成了可增删的档案列表），但键必须留着 ——
 * 上面那三个地方要逐字对齐。
 */
export const PRESETS = {
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-flash" },
  qwen: {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen3.8-max",
  },
  kimi: { label: "Kimi (月之暗面)", baseUrl: "https://api.moonshot.cn/v1", model: "kimi-k3" },
  glm: { label: "智谱 GLM", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6" },
  mimo: { label: "小米 MiMo", baseUrl: "https://api.xiaomimimo.com/v1", model: "mimo-v2.6-pro" },
  // Zen is restricted to its free tier by the backend; the label must not imply
  // that paid ids are selectable here.
  opencode_zen: {
    label: "OpenCode Zen（仅免费模型）",
    baseUrl: "https://opencode.ai/zen/v1",
    model: "mimo-v2.6-flash-free",
  },
  opencode_go: {
    label: "OpenCode Go（$10/月订阅）",
    baseUrl: "https://opencode.ai/zen/go/v1",
    model: "mimo-v2.6-pro",
  },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5.6-terra" },
  custom: { label: "自定义", baseUrl: "", model: "" },
} as const satisfies Record<ModelPreset, { label: string; baseUrl: string; model: string }>;

/** 下拉框里「内置服务商」那一组；`custom` 是过去式，交给「新建自定义供应商」。 */
export const BUILTIN_PRESETS = (Object.keys(PRESETS) as ModelPreset[]).filter(
  (value) => value !== LEGACY_CUSTOM_PRESET,
);

/** 某个供应商的显示名。内置的来自上面那张表，自定义的由用户起名。 */
export function providerLabel(preset: string, customLabel = ""): string {
  const builtin = (PRESETS as Record<string, { label: string } | undefined>)[preset];
  if (builtin) return builtin.label;
  return customLabel.trim() || DEFAULT_CUSTOM_LABEL;
}

/** 内置供应商的默认连接参数；自定义供应商没有内置默认值。 */
export function presetDefaults(preset: string): { baseUrl: string; model: string } {
  const builtin = (PRESETS as Record<string, { baseUrl: string; model: string } | undefined>)[
    preset
  ];
  return builtin ?? { baseUrl: "", model: "" };
}
