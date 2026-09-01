import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  Library,
  MessageSquarePlus,
  PanelRightClose,
  Search,
  Settings,
} from "lucide-react";
import { clsx } from "clsx";
import { IconButton } from "../components/IconButton";
import { SettingsView } from "../features/settings/SettingsView";
import { useUiStore } from "../stores/ui-store";

export function Workspace() {
  const activeView = useUiStore((state) => state.activeView);
  const setActiveView = useUiStore((state) => state.setActiveView);
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const referencePanelOpen = useUiStore((state) => state.referencePanelOpen);
  const setReferencePanelOpen = useUiStore((state) => state.setReferencePanelOpen);

  return (
    <main
      aria-label="工作台"
      className={clsx(
        "workspace-grid",
        sidebarCollapsed && "sidebar-is-collapsed",
        !referencePanelOpen && "reference-is-closed",
      )}
    >
      <aside
        aria-label="主导航"
        className={clsx("workspace-sidebar w-[248px]", sidebarCollapsed && "is-collapsed")}
      >
        <div className="brand-row">
          <span className="brand-mark" aria-hidden="true">
            D
          </span>
          <strong>DocMind</strong>
          <IconButton
            icon={
              sidebarCollapsed ? (
                <ChevronRight aria-hidden="true" size={18} />
              ) : (
                <ChevronLeft aria-hidden="true" size={18} />
              )
            }
            label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            onClick={toggleSidebar}
            size="small"
          />
        </div>
        <button aria-label="新建会话" className="new-chat-button" title="新建会话" type="button">
          <MessageSquarePlus aria-hidden="true" size={17} />
          <span>新建会话</span>
        </button>
        <nav className="nav-list" aria-label="功能导航">
          <button aria-label="知识库" title="知识库" type="button">
            <Library aria-hidden="true" size={17} />
            <span>知识库</span>
          </button>
          <button aria-label="导入文档" title="导入文档" type="button">
            <FilePlus2 aria-hidden="true" size={17} />
            <span>导入文档</span>
          </button>
        </nav>
        <div className="sidebar-library">
          <div className="sidebar-section-title">
            <span>知识库</span>
            <IconButton
              icon={<Search aria-hidden="true" size={16} />}
              label="搜索知识库"
              size="small"
            />
          </div>
          <p>登录语雀后，知识库将显示在这里。</p>
        </div>
        <button
          aria-label="设置"
          className={clsx("settings-nav", activeView === "settings" && "is-active")}
          onClick={() => setActiveView(activeView === "settings" ? "workspace" : "settings")}
          title="设置"
          type="button"
        >
          <Settings aria-hidden="true" size={17} />
          <span>设置</span>
        </button>
      </aside>
      <section
        className="workspace-main"
        aria-label={activeView === "settings" ? "设置内容" : "对话工作区"}
      >
        {activeView === "settings" ? (
          <SettingsView />
        ) : (
          <div className="empty-workspace">
            <BookOpen aria-hidden="true" size={24} />
            <h1>选择知识库开始对话</h1>
            <p>导入文档后，可在这里检索内容并查看引用来源。</p>
            <button className="button button-primary" type="button">
              <FilePlus2 aria-hidden="true" size={16} />
              导入第一篇文档
            </button>
          </div>
        )}
      </section>
      <aside aria-label="引用资料" className="workspace-reference w-[320px]">
        <header>
          <div>
            <span>引用资料</span>
            <small>0 项</small>
          </div>
          <IconButton
            icon={<PanelRightClose aria-hidden="true" size={17} />}
            label="关闭引用资料"
            onClick={() => setReferencePanelOpen(false)}
            size="small"
          />
        </header>
        <div className="reference-empty">
          <BookOpen aria-hidden="true" size={21} />
          <p>点击回答中的引用，可在此查看原文。</p>
        </div>
      </aside>
    </main>
  );
}
