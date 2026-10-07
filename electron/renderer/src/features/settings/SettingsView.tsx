import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { SettingsNav } from "./SettingsNav";
import { clientErrorMessage, useSettingsQuery } from "./settings.queries";
import {
  defaultSettingsModule,
  settingsModuleTitleId,
  settingsModules,
  type SettingsModule,
} from "./settings-modules";
// Imported for its side effect: this is what populates the registry with the
// built-in pages. The view must not enumerate them itself.
import "./builtin-settings-modules";

/**
 * One module's body, plus the heading it owns.
 *
 * `hidden` rather than unmounting is load-bearing. The model form holds a dozen
 * `useState` drafts — an API key typed but not yet saved, a half-chosen preset.
 * Unmounting on switch would drop them, so a user who typed a key, went to look
 * at another page and came back would find the field empty. Keeping every
 * mounted module in the DOM preserves that state and keeps `plugins.list` to a
 * single request, while `hidden` takes the inactive ones out of the tab order
 * and the accessibility tree.
 */
function ModulePanel({ module, active }: { module: SettingsModule; active: boolean }) {
  const titleId = settingsModuleTitleId(module.id);
  const Icon = module.icon;
  return (
    <section
      aria-labelledby={titleId}
      className="settings-section"
      hidden={!active}
      id={module.id}
      tabIndex={-1}
    >
      <div className="section-heading">
        <Icon aria-hidden="true" size={18} />
        <div>
          <h2 id={titleId}>{module.label}</h2>
          {module.description ? <p>{module.description}</p> : null}
        </div>
      </div>
      {module.render()}
    </section>
  );
}

export function SettingsView() {
  const settings = useSettingsQuery();
  const modules = settingsModules();
  const [activeId, setActiveId] = useState<string | null>(null);

  // Default to the first registered module rather than hard-coding an id, so a
  // page inserted at the front becomes the landing page without an edit here.
  useEffect(() => {
    if (activeId !== null && modules.some((module) => module.id === activeId)) return;
    setActiveId(defaultSettingsModule()?.id ?? null);
  }, [activeId, modules]);

  // Re-anchor after a page switch so a keyboard user lands on the new heading
  // instead of wherever they were in the previous panel.
  const focusActive = useCallback(() => {
    if (activeId === null) return;
    document.getElementById(activeId)?.focus({ preventScroll: true });
  }, [activeId]);

  useEffect(() => {
    focusActive();
  }, [focusActive]);

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

  const active = modules.find((module) => module.id === activeId) ?? modules[0];

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
          <SettingsNav activeId={active?.id ?? null} onSelect={setActiveId} />
        </div>
        <div className="settings-sections">
          {modules.map((module) => (
            <ModulePanel active={module.id === active?.id} key={module.id} module={module} />
          ))}
        </div>
      </div>
    </div>
  );
}
