/**
 * Built-in settings modules.
 *
 * Each page on the settings screen registers itself here. This file is the
 * *only* place a built-in page is declared — the view and the nav both read the
 * registry, so adding a page means adding one `registerModule` call below and
 * touching nothing else.
 *
 * Importing this file is what populates the registry, so it is imported for
 * its side effects by `SettingsView` exactly once. Registration order is only a
 * tiebreaker; `order` decides the sequence.
 */
import type { ReactNode } from "react";
import { Cpu, HardDrive, KeyRound, Puzzle } from "lucide-react";
import type { SettingsView } from "../../../../shared/contracts";
import { DiagnosticsSection } from "./DiagnosticsSection";
import { ModelSettingsForm } from "./ModelSettingsForm";
import { PluginSettings } from "./PluginSettings";
import { RuntimeModelSettings } from "./RuntimeModelSettings";
import { registerModule } from "./settings-modules";
import { useSettingsQuery } from "./settings.queries";

/**
 * A module body reads the settings snapshot itself.
 *
 * Handing every module its settings as a prop would mean the registry had to
 * know the shape of the payload, and a module contributed at runtime would need
 * new wiring to receive data it already knows how to fetch. Reading the shared
 * query keeps the registry free of any knowledge about settings.
 */
function useLoadedSettings() {
  const settings = useSettingsQuery();
  // `SettingsView` renders modules only once the snapshot resolved, so this is
  // unreachable in practice. Returning null rather than throwing keeps a
  // transient cache eviction from taking the whole page down.
  return settings.data ?? null;
}

/**
 * Wraps a section component that needs the settings snapshot.
 *
 * Four near-identical wrappers used to stand here. They collapse into this one
 * generic bound component because they differed only in which form they
 * rendered — and having them inline also tripped `react-refresh`, which (with
 * lint at `--max-warnings=0`) rejects a module whose exports are not components.
 */
function settingsSection(
  Section: (props: { settings: SettingsView }) => ReactNode,
): (() => ReactNode) {
  return function SettingsSectionModule() {
    const settings = useLoadedSettings();
    if (!settings) return null;
    return Section({ settings });
  };
}

const ModelModule = settingsSection(ModelSettingsForm);
const RuntimeModule = settingsSection(RuntimeModelSettings);
const DiagnosticsModule = settingsSection(DiagnosticsSection);

/**
 * Marks this module as the built-in registration side-effect module.
 *
 * `react-refresh/only-export-components` (lint runs with `--max-warnings=0`)
 * rejects a module that exists only for its side effects, since that silently
 * defeats hot reloading. An exported binding nothing imports is the
 * conventional way to declare "imported for its effects, and that is on
 * purpose".
 */
export const BUILTIN_SETTINGS_MODULES_LOADED = true;

registerModule({
  id: "settings-model",
  label: "对话模型",
  description: "使用 OpenAI 兼容接口连接模型服务",
  icon: KeyRound,
  order: 10,
  render: () => <ModelModule />,
});

registerModule({
  id: "settings-runtime",
  label: "本地运行与路由",
  description: "配置本地模型与云端回退策略",
  icon: Cpu,
  order: 20,
  render: () => <RuntimeModule />,
});

registerModule({
  id: "settings-plugins",
  label: "插件",
  icon: Puzzle,
  order: 30,
  render: () => <PluginSettings />,
});

registerModule({
  id: "settings-diagnostics",
  label: "本地数据与诊断",
  description: "查看数据位置并管理失败截图",
  icon: HardDrive,
  order: 50,
  render: () => <DiagnosticsModule />,
});
