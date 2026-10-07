import type { ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { DiagnosticsSection } from "./DiagnosticsSection";
import { ModelSettingsForm } from "./ModelSettingsForm";
import { PluginSettings } from "./PluginSettings";
import { SettingsNav } from "./SettingsNav";
import { WebSearchSettings } from "./WebSearchSettings";
import { RuntimeModelSettings } from "./RuntimeModelSettings";
import {
  SETTINGS_SECTIONS,
  settingsSectionTitleId,
  type SettingsSectionMeta,
} from "./settings-sections";
import { clientErrorMessage, useSettingsQuery } from "./settings.queries";

function SettingsSection({
  meta,
  description,
  children,
}: {
  meta: SettingsSectionMeta;
  /**
   * Optional because a section may own its own heading copy: the plugin
   * section derives its line from whatever is installed at runtime, so a
   * static string here would have to hard-code vendor names.
   */
  description?: string;
  children: ReactNode;
}) {
  const titleId = settingsSectionTitleId(meta.id);
  const Icon = meta.icon;
  return (
    <section aria-labelledby={titleId} className="settings-section" id={meta.id} tabIndex={-1}>
      <div className="section-heading">
        <Icon aria-hidden="true" size={18} />
        <div>
          <h2 id={titleId}>{meta.label}</h2>
          {description ? <p>{description}</p> : null}
        </div>
      </div>
      {children}
    </section>
  );
}

export function SettingsView() {
  const settings = useSettingsQuery();

  if (settings.isPending) return <div className="settings-loading">正在读取设置…</div>;
  if (settings.isError) {
    return (
      <div className="settings-error" role="alert">
        <p>{clientErrorMessage(settings.error)}</p>
        <button className="button button-secondary" onClick={() => settings.refetch()}>
          <RefreshCw aria-hidden="true" size={16} />
          重新加载设置
        </button>
      </div>
    );
  }

  return (
    <div className="settings-view">
      <header className="view-header">
        <div>
          <h1>设置</h1>
          <p>管理模型、连接绑定和本地诊断信息</p>
        </div>
      </header>
      <div className="settings-body">
        <div className="settings-subnav-column">
          <SettingsNav />
        </div>
        <div className="settings-sections">
          <SettingsSection
            description="使用 OpenAI 兼容接口连接模型服务"
            meta={SETTINGS_SECTIONS.model}
          >
            <ModelSettingsForm settings={settings.data} />
          </SettingsSection>
          <SettingsSection
            description="配置本地模型与云端回退策略"
            meta={SETTINGS_SECTIONS.runtime}
          >
            <RuntimeModelSettings settings={settings.data} />
          </SettingsSection>
          <SettingsSection meta={SETTINGS_SECTIONS.plugins}>
            <PluginSettings />
          </SettingsSection>
          <SettingsSection
            description="本地证据不足时按模型内置联网、Tavily、免费兜底的顺序搜索"
            meta={SETTINGS_SECTIONS.webSearch}
          >
            <WebSearchSettings settings={settings.data} />
          </SettingsSection>
          <SettingsSection
            description="查看数据位置并管理失败截图"
            meta={SETTINGS_SECTIONS.diagnostics}
          >
            <DiagnosticsSection settings={settings.data} />
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}
