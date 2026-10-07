import { Bell, Blocks, KeyRound, LogIn, Puzzle, Search, X } from "lucide-react";
import { useDeferredValue, useId, useMemo, useState } from "react";
import type { PluginManifest } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import { PluginSecretForm } from "./PluginSecretForm";
import { PluginLogin } from "./PluginLogin";
import { filterPlugins } from "./plugin-filter";
import { usePluginDiagnosticsQuery, usePluginsQuery } from "./settings.queries";

/**
 * The plugin page: a flat, searchable grid of plugin cards.
 *
 * Two deliberate non-decisions, both of which used to be the wrong behaviour:
 *
 * - **No grouping.** Cards used to be partitioned into 「知识库来源 / 通知」 and
 *   before that grouped by vendor. Both were the app's opinion about how the
 *   user should think about its own integrations, and both made a bot webhook
 *   read as a third way to import documents. `purpose` is still shown, but as a
 *   tag on the card — metadata, not a section heading.
 * - **No vendor list here.** Every card comes from `GET /api/plugins`, which
 *   derives them from the backend's provider registry. The plugin count, their
 *   names, and their searchability all follow from what is installed; nothing
 *   in this file has to change when a plugin is added.
 */
export function PluginSettings() {
  const [query, setQuery] = useState("");
  const searchId = useId();
  // Filtering happens in the renderer against the already-loaded catalogue
  // rather than round-tripping to the backend per keystroke: the response is
  // small, and a local filter keeps typing instant. The backend's `q` endpoint
  // exists for the case where the catalogue grows past what is worth shipping
  // wholesale.
  const plugins = usePluginsQuery();
  const deferredQuery = useDeferredValue(query);
  const diagnostics = usePluginDiagnosticsQuery();

  const visible = useMemo(
    () => filterPlugins(plugins.data ?? [], deferredQuery),
    [plugins.data, deferredQuery],
  );
  const total = plugins.data?.length ?? 0;

  return (
    <div className="plugin-settings">
      <div className="plugin-search">
        <Search aria-hidden="true" size={15} />
        <input
          aria-label="搜索插件"
          id={searchId}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索插件名称、功能或来源"
          type="search"
          value={query}
        />
        {query ? (
          <button
            aria-label="清除搜索"
            className="plugin-search-clear"
            onClick={() => setQuery("")}
            type="button"
          >
            <X aria-hidden="true" size={14} />
          </button>
        ) : null}
      </div>

      <p className="plugin-toolbar-note">
        {plugins.isPending
          ? "正在加载插件…"
          : total === 0
            ? "当前没有可用插件。"
            : deferredQuery.trim()
              ? `找到 ${visible.length} 个匹配「${deferredQuery.trim()}」的插件，共 ${total} 个`
              : `共 ${total} 个插件，均为可选连接，不影响 DocMind 启动`}
      </p>

      {plugins.isError ? (
        <p className="inline-error" role="alert">
          插件清单加载失败：{plugins.errorMessage}
        </p>
      ) : null}

      {!plugins.isPending && visible.length === 0 && total > 0 ? (
        <p className="muted-row">没有匹配「{deferredQuery.trim()}」的插件，换个关键词试试。</p>
      ) : null}

      <div className="plugin-grid">
        {visible.map((plugin) => (
          <PluginCard key={plugin.id} plugin={plugin} />
        ))}
      </div>

      <PluginLoadFailures diagnostics={diagnostics.data ?? []} />
    </div>
  );
}

/**
 * Plugins that failed to load, surfaced rather than swallowed. A plugin that
 * silently failed is indistinguishable from one that was never installed, so
 * the user would have no way to tell "this integration is broken" from "this
 * integration does not exist".
 */
function PluginLoadFailures({ diagnostics }: { diagnostics: { name: string; error: string }[] }) {
  const failures = diagnostics.filter((item) => item.error);
  if (failures.length === 0) return null;
  return (
    <div className="plugin-load-failures" role="status">
      <strong>以下插件加载失败</strong>
      <ul>
        {failures.map((failure) => (
          <li key={failure.name}>
            <code>{failure.name}</code>：{failure.error}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PluginCard({ plugin }: { plugin: PluginManifest }) {
  const Icon = iconFor(plugin.icon);
  const connected = plugin.state === "verified";
  const verified = plugin.state === "verified";

  return (
    <article className="plugin-card" aria-labelledby={`plugin-${plugin.id}-title`}>
      <header>
        <Icon aria-hidden="true" size={17} />
        <div className="plugin-card-heading">
          <h3 id={`plugin-${plugin.id}-title`}>{plugin.label}</h3>
          {/* Which integration this plugs into. Without it a card titled
              「机器人」 or 「网页登录」 says nothing about what it connects. */}
          <p className="plugin-card-owner">{plugin.providerLabel}</p>
        </div>
        <StatusBadge
          label={connected ? "已连接" : verified ? "待验证" : "未连接"}
          tone={connected ? "success" : verified ? "pending" : "neutral"}
        />
      </header>

      {plugin.summary ? <p className="plugin-card-summary">{plugin.summary}</p> : null}

      {/* Purpose is a tag, not a heading: it is useful next to the title and
          actively harmful as a section divider. */}
      <p className="plugin-card-tags">
        <span className="plugin-tag">
          {plugin.purpose === "notify" ? "通知" : "知识库"}
        </span>
        {plugin.version ? <span className="plugin-tag">v{plugin.version}</span> : null}
        {plugin.homepage ? (
          <a
            className="plugin-tag plugin-tag-link"
            href={plugin.homepage}
            onClick={(event) => {
              event.preventDefault();
              void window.docmind.shell.openExternal(plugin.homepage!);
            }}
          >
            了解更多
          </a>
        ) : null}
      </p>

      {plugin.hint ? <p className="plugin-card-hint">{plugin.hint}</p> : null}

      <div className="plugin-card-body">
        {plugin.hasSecret ? (
          <PluginSecretForm plugin={plugin} />
        ) : (
          <PluginLogin plugin={plugin} />
        )}
      </div>
    </article>
  );
}

/**
 * Icon keys are declared by plugins as data, so the renderer owns the glyph
 * table. An unknown key falls back to a neutral mark rather than rendering
 * nothing — a third-party plugin may ship an icon this build has never heard
 * of, and it must still look like a card.
 */
const PLUGIN_ICONS: Record<string, typeof Blocks> = {
  library: Blocks,
  key: KeyRound,
  login: LogIn,
  bell: Bell,
};

function iconFor(key: string | null | undefined) {
  return (key && PLUGIN_ICONS[key]) || Puzzle;
}