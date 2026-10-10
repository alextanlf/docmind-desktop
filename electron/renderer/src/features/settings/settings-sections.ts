import { Cpu, Database, Globe, HardDrive, KeyRound, Link2, type LucideIcon } from "lucide-react";

export interface SettingsSectionMeta {
  id: string;
  label: string;
  icon: LucideIcon;
}

export const SETTINGS_SECTIONS = {
  model: { id: "settings-model", label: "对话模型", icon: KeyRound },
  embedding: { id: "settings-embedding", label: "Embedding 模型", icon: Database },
  runtime: { id: "settings-runtime", label: "本地运行与路由", icon: Cpu },
  connections: { id: "settings-connections", label: "连接与绑定", icon: Link2 },
  webSearch: { id: "settings-web-search", label: "联网搜索", icon: Globe },
  diagnostics: { id: "settings-diagnostics", label: "本地数据与诊断", icon: HardDrive },
} as const satisfies Record<string, SettingsSectionMeta>;

export const SETTINGS_SECTION_ORDER = [
  SETTINGS_SECTIONS.model,
  SETTINGS_SECTIONS.embedding,
  SETTINGS_SECTIONS.runtime,
  SETTINGS_SECTIONS.connections,
  SETTINGS_SECTIONS.webSearch,
  SETTINGS_SECTIONS.diagnostics,
] satisfies SettingsSectionMeta[];

export function settingsSectionTitleId(id: string) {
  return `${id}-title`;
}
