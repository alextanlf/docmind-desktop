import { Database, Globe, KeyRound, Link2, RefreshCw } from "lucide-react";
import { ConnectionSettings } from "./ConnectionSettings";
import { DiagnosticsSection } from "./DiagnosticsSection";
import { EmbeddingStatus } from "./EmbeddingStatus";
import { ModelSettingsForm } from "./ModelSettingsForm";
import { WebSearchSettings } from "./WebSearchSettings";
import { RuntimeModelSettings } from "./RuntimeModelSettings";
import { clientErrorMessage, useSettingsQuery } from "./settings.queries";

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
          <p>管理模型、连接绑定、Embedding 和本地诊断信息</p>
        </div>
      </header>
      <section className="settings-section" aria-labelledby="model-settings-title">
        <div className="section-heading">
          <KeyRound aria-hidden="true" size={18} />
          <div>
            <h2 id="model-settings-title">对话模型</h2>
            <p>使用 OpenAI 兼容接口连接模型服务</p>
          </div>
        </div>
        <ModelSettingsForm settings={settings.data} />
      </section>
      <section className="settings-section" aria-labelledby="embedding-settings-title">
        <div className="section-heading">
          <Database aria-hidden="true" size={18} />
          <div>
            <h2 id="embedding-settings-title">Embedding 模型</h2>
            <p>本地生成文档向量，不上传原文</p>
          </div>
        </div>
        <EmbeddingStatus />
      </section>
      <section className="settings-section" aria-labelledby="runtime-settings-title">
        <div className="section-heading">
          <KeyRound aria-hidden="true" size={18} />
          <div>
            <h2 id="runtime-settings-title">本地运行与路由</h2>
            <p>配置 Ollama 与云端回退策略</p>
          </div>
        </div>
        <RuntimeModelSettings settings={settings.data} />
      </section>
      <section className="settings-section" aria-labelledby="connections-settings-title">
        <div className="section-heading">
          <Link2 aria-hidden="true" size={18} />
          <div>
            <h2 id="connections-settings-title">连接与绑定</h2>
            <p>语雀网页、语雀 API 和飞书均为可选连接，不影响 DocMind 启动</p>
          </div>
        </div>
        <ConnectionSettings settings={settings.data} />
      </section>
      <section className="settings-section" aria-labelledby="web-search-settings-title">
        <div className="section-heading">
          <Globe aria-hidden="true" size={18} />
          <div>
            <h2 id="web-search-settings-title">联网搜索</h2>
            <p>本地证据不足时按模型内置联网、Tavily、免费兜底的顺序搜索</p>
          </div>
        </div>
        <WebSearchSettings settings={settings.data} />
      </section>
      <section className="settings-section" aria-labelledby="diagnostics-settings-title">
        <div className="section-heading">
          <Database aria-hidden="true" size={18} />
          <div>
            <h2 id="diagnostics-settings-title">本地数据与诊断</h2>
            <p>查看数据位置并管理失败截图</p>
          </div>
        </div>
        <DiagnosticsSection settings={settings.data} />
      </section>
    </div>
  );
}
