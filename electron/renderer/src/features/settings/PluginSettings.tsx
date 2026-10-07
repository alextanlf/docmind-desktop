import { Bell, Blocks, KeyRound, LogIn, Puzzle, Search, X } from "lucide-react";
import { useDeferredValue, useId, useMemo, useState, type ReactNode } from "react";
import type { PluginDiagnostic, PluginManifest } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import { PluginSecretForm } from "./PluginSecretForm";
import { PluginLogin } from "./PluginLogin";
import { filterPlugins } from "./plugin-filter";
import { usePluginDirectoryQuery, usePluginDiagnosticsQuery, usePluginsQuery } from "./settings.queries";

/**
 * The plugin page: a flat, searchable grid of plugin cards.
 *
 * Three deliberate non-decisions, each of which used to be the wrong behaviour:
 *
 * - **No grouping.** Cards used to be partitioned into 「知识库来源 / 通知」 and
 *   before that grouped by vendor. Both were the app's opinion about how the
 *   user should think about its own integrations, and both made a bot webhook
 *   read as a third way to import documents. The category is still shown, but as
 *   a tag the plugin declares — not a section heading, and not copy this file
 *   makes up.
 * - **No vendor list here.** Every card comes from `GET /api/plugins`, which
 *   derives them from what is installed. The plugin count, their names, and
 *   their searchability all follow from that; nothing in this file has to change
 *   when a plugin is added.
 * - **No fixed set of capabilities.** `kind` selects a card *body*, so a plugin
 *   contributing something this build has never seen still renders as a card
 *   rather than disappearing.
 */
export function PluginSettings() {
  const [query, setQuery] = useState("");
  const searchId = useId();
  // Filtering happens in the renderer against the already-loaded catalogue
  // rather than round-tripping to the backend per keystroke: the response is
  // small, and a local filter keeps typing instant. The backend's `q` endpoint
  // exists for the case where the catalogue grows past what is worth shipping
  // wholesale, and both apply the same rule.
  const plugins = usePluginsQuery();
  const deferredQuery = useDeferredValue(query);
  const diagnostics = usePluginDiagnosticsQuery();
  const directory = usePluginDirectoryQuery();

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

      <PluginDirectoryNote directory={directory.data?.path} />
    </div>
  );
}

/**
 * Where to put a plugin. Shown because installing one is a filesystem action:
 * every other part of the flow is discoverable from the UI, and this is the one
 * piece the user cannot guess — the path follows the data directory, which is
 * not the same on every platform or every build.
 */
function PluginDirectoryNote({ directory }: { directory?: string }) {
  if (!directory) return null;
  return (
    <p className="plugin-directory">
      插件目录：<code>{directory}</code>
      <span>
        把插件目录（或 git clone 下来的仓库）放进这里，重启 DocMind 后生效。
      </span>
    </p>
  );
}

/**
 * Plugins that failed to load, surfaced rather than swallowed. A plugin that
 * silently failed is indistinguishable from one that was never installed, so
 * the user would have no way to tell "this integration is broken" from "this
 * integration does not exist".
 *
 * A failure that came from a directory also shows the directory: it is the only
 * part a developer can act on, and guessing which of several clones is at fault
 * is exactly the work this panel exists to remove.
 */
function PluginLoadFailures({ diagnostics }: { diagnostics: PluginDiagnostic[] }) {
  const failures = diagnostics.filter((item) => item.error);
  if (failures.length === 0) return null;
  return (
    <div className="plugin-load-failures" role="status">
      <strong>以下插件加载失败</strong>
      <ul>
        {failures.map((failure) => (
          <li key={failure.name}>
            <code>{failure.name}</code>：{failure.error}
            {failure.source ? <span className="plugin-failure-source">{failure.source}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PluginCard({ plugin }: { plugin: PluginManifest }) {
  const Icon = iconFor(plugin.icon);
  const verified = plugin.state === "verified";
  // Only a card backed by a live credential has a connection to report.
  const tracksCredential = CREDENTIAL_KINDS.has(plugin.kind);

  return (
    <article className="plugin-card" aria-labelledby={`plugin-${plugin.id}-title`}>
      <header>
        <Icon aria-hidden="true" size={17} />
        <div className="plugin-card-heading">
          <h3 id={`plugin-${plugin.id}-title`}>{plugin.label}</h3>
          {/* Which integration this plugs into. Without it a card titled
              「机器人」 or 「网页登录」 says nothing about what it connects. */}
          {plugin.providerLabel ? (
            <p className="plugin-card-owner">{plugin.providerLabel}</p>
          ) : null}
        </div>
        {tracksCredential ? (
          <StatusBadge
            label={verified ? "已连接" : plugin.configured ? "待验证" : "未连接"}
            tone={verified ? "success" : plugin.configured ? "pending" : "neutral"}
          />
        ) : null}
      </header>

      {plugin.summary ? <p className="plugin-card-summary">{plugin.summary}</p> : null}

      {/* The category is a tag, not a heading: it is useful next to the title and
          actively harmful as a section divider. Its text is declared by the
          plugin, so this file never has to know what a category is called. */}
      {plugin.tag || plugin.version || plugin.homepage ? (
        <p className="plugin-card-tags">
          {plugin.tag ? <span className="plugin-tag">{plugin.tag}</span> : null}
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
      ) : null}

      {plugin.hint ? <p className="plugin-card-hint">{plugin.hint}</p> : null}

      <div className="plugin-card-body">{cardBody(plugin)}</div>
    </article>
  );
}

/**
 * Card bodies by contribution kind.
 *
 * The renderer owns this vocabulary for the same reason it owns the glyph
 * table: a plugin ships as data, so it cannot ship a component. An unknown kind
 * simply gets no body — the card still renders with its declared copy, which
 * beats hiding a plugin that did install.
 */
const CARD_BODIES: Record<string, (plugin: PluginManifest) => ReactNode> = {
  remote_source: (plugin) =>
    plugin.hasSecret ? <PluginSecretForm plugin={plugin} /> : <PluginLogin plugin={plugin} />,
  document_format: (plugin) =>
    plugin.extensions.length === 0 ? null : (
      <p className="plugin-format-extensions">
        <span>支持的文件类型</span>
        {plugin.extensions.map((extension) => (
          <code key={extension}>{extension}</code>
        ))}
      </p>
    ),
};

function cardBody(plugin: PluginManifest): ReactNode {
  const render = CARD_BODIES[plugin.kind];
  return render ? render(plugin) : null;
}

/** Kinds whose card tracks a live credential, and so reports a connection. */
const CREDENTIAL_KINDS = new Set(["remote_source"]);

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
