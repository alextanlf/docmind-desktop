import {
  ArrowLeft,
  Bell,
  Blocks,
  ChevronRight,
  KeyRound,
  LoaderCircle,
  LogIn,
  Power,
  Puzzle,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useDeferredValue, useId, useMemo, useRef, useState, type ReactNode } from "react";
import type { PluginManifest } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import { PluginLogin } from "./PluginLogin";
import { PluginSecretForm } from "./PluginSecretForm";
import { filterPlugins } from "./plugin-filter";
import { pluginStatus, provenanceLabel, removalHint } from "./plugin-presentation";
import {
  clientErrorMessage,
  usePluginDirectoryQuery,
  usePluginsQuery,
  useSetPluginEnabledMutation,
  useUninstallPluginMutation,
} from "./settings.queries";

/**
 * The plugin page: a list you can look through, and one plugin behind a click.
 *
 * It used to be a single wall of cards with every credential form open at once,
 * which had two problems worth naming. There was no level at which a user could
 * see *what they had* — only what they could configure — and a plugin with
 * nothing to configure had nowhere to be, which is every plugin that is switched
 * off or failed to load.
 *
 * So there are two levels. The list answers "what is installed, and what is each
 * one for"; the detail answers "what is this, where did it come from, and do I
 * want it".
 *
 * The selection is a row id with the plugin's name kept beside it, and the name
 * is the fallback. Switching a plugin off replaces its cards with a row built
 * from its own record, so the id the user clicked stops existing — and being
 * thrown back to the list by the change they just made is the wrong answer to
 * "what did that do?".
 *
 * Three deliberate non-decisions, each of which used to be the wrong behaviour:
 *
 * - **No grouping.** Rows used to be partitioned into 「知识库来源 / 通知」 and
 *   before that grouped by vendor. Both were the app's opinion about how the
 *   user should think about its own integrations, and both made a bot webhook
 *   read as a third way to import documents. The category is still shown, but as
 *   a tag the plugin declares — not a section heading, and not copy this file
 *   makes up.
 * - **No vendor list here.** Every row comes from `GET /api/plugins`, which
 *   derives them from what is installed. The plugin count, their names, and their
 *   searchability all follow from that; nothing in this file has to change when a
 *   plugin is added.
 * - **No fixed set of capabilities.** `kind` selects a card *body*, so a plugin
 *   contributing something this build has never seen still renders as a card
 *   rather than disappearing.
 */
export function PluginSettings() {
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const searchId = useId();
  // Filtering happens in the renderer against the already-loaded catalogue
  // rather than round-tripping to the backend per keystroke: the response is
  // small, and a local filter keeps typing instant. The backend's `q` endpoint
  // exists for the case where the catalogue grows past what is worth shipping
  // wholesale, and both apply the same rule.
  const plugins = usePluginsQuery();
  const directory = usePluginDirectoryQuery();
  const deferredQuery = useDeferredValue(query);

  // Memoised because the `?? []` would otherwise be a new array every render,
  // which invalidates the two derivations below it on every render — the rule
  // that notices is right, and the fix is here rather than in its dependencies.
  const rows = useMemo(() => plugins.data ?? [], [plugins.data]);
  const visible = useMemo(() => filterPlugins(rows, deferredQuery), [rows, deferredQuery]);
  const total = rows.length;

  const selected = useMemo(() => {
    if (selection === null) return null;
    return (
      rows.find((row) => row.id === selection.id) ??
      (selection.plugin === null
        ? undefined
        : rows.find((row) => row.origin?.plugin === selection.plugin)) ??
      null
    );
  }, [rows, selection]);

  if (selected !== null) {
    return (
      <PluginDetail
        plugin={selected}
        onBack={() => setSelection(null)}
        onRemoved={(message) => {
          // The row is about to stop existing, so the detail cannot be the one
          // holding this message — it unmounts, and the user is right back at
          // the list wondering what happened.
          setNotice(message);
          setSelection(null);
        }}
      />
    );
  }

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

      {notice !== null ? (
        <p className="plugin-notice" role="status">
          {notice}
        </p>
      ) : null}

      {!plugins.isPending && visible.length === 0 && total > 0 ? (
        <p className="muted-row">没有匹配「{deferredQuery.trim()}」的插件，换个关键词试试。</p>
      ) : null}

      <ul className="plugin-list">
        {visible.map((plugin) => (
          <li key={plugin.id}>
            <PluginRow
              plugin={plugin}
              onOpen={() =>
                setSelection({ id: plugin.id, plugin: plugin.origin?.plugin ?? null })
              }
            />
          </li>
        ))}
      </ul>

      <PluginDirectoryNote directory={directory.data?.path} />
    </div>
  );
}

type Selection = { id: string; plugin: string | null };

/**
 * One plugin, one line of it.
 *
 * The row is the whole click target rather than a link inside it: the detail is
 * the only place a plugin can be acted on, so every part of the row has to lead
 * there. The status chip is on the row as well, because "is this working" is
 * what decides whether a user needs to open it at all.
 */
function PluginRow({ plugin, onOpen }: { plugin: PluginManifest; onOpen: () => void }) {
  const Icon = iconFor(plugin.icon);
  const status = pluginStatus(plugin);
  // A plugin with no declared copy still has something worth reading in its
  // second line: why it is not working.
  const line = plugin.summary ?? plugin.origin?.error ?? null;

  return (
    <button className="plugin-row" onClick={onOpen} type="button">
      <span className="plugin-row-icon">
        <Icon aria-hidden="true" size={17} />
      </span>
      <span className="plugin-row-text">
        <span className="plugin-row-title">
          <strong>{plugin.label}</strong>
          {/* Which integration this plugs into. Without it a row titled
              「机器人」 or 「网页登录」 says nothing about what it connects. */}
          {plugin.providerLabel ? (
            <span className="plugin-row-owner">{plugin.providerLabel}</span>
          ) : null}
        </span>
        {line !== null ? <span className="plugin-row-summary">{line}</span> : null}
      </span>
      <span className="plugin-row-meta">
        {plugin.tag ? <span className="plugin-tag">{plugin.tag}</span> : null}
        {status ? <StatusBadge label={status.label} tone={status.tone} /> : null}
        <ChevronRight aria-hidden="true" className="plugin-row-chevron" size={16} />
      </span>
    </button>
  );
}

/**
 * One plugin, in full.
 *
 * Everything on screen is declared by the plugin — its copy, its version, its
 * homepage, and what its row contributes — plus the two facts only the server
 * knows: where the plugin came from, and which of the two actions it can support.
 * The page never decides either for itself.
 */
function PluginDetail({
  plugin,
  onBack,
  onRemoved,
}: {
  plugin: PluginManifest;
  onBack: () => void;
  onRemoved: (message: string) => void;
}) {
  const Icon = iconFor(plugin.icon);
  const origin = plugin.origin ?? null;
  const status = pluginStatus(plugin);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Hooked unconditionally, with the name the actions would use. Both hooks are
  // inert until called, and the buttons that call them are not rendered without
  // an origin — a conditional hook would be the worse trade.
  const toggle = useSetPluginEnabledMutation(origin?.plugin ?? "");
  const remove = useUninstallPluginMutation(origin?.plugin ?? "");
  const busy = toggle.isPending || remove.isPending;

  async function switchPlugin(enabled: boolean) {
    setError(null);
    try {
      await toggle.mutateAsync(enabled);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }

  async function removePlugin() {
    setError(null);
    try {
      const result = await remove.mutateAsync();
      setConfirming(false);
      // Where it went, not "moved to the trash": the destination differs per
      // platform, and a user who wants the plugin back needs the real one.
      onRemoved(
        result.removedTo
          ? `已把「${plugin.label}」移出插件目录：${result.removedTo}。重启 DocMind 后不再加载。`
          : `已把「${plugin.label}」移出插件目录。重启 DocMind 后不再加载。`,
      );
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }

  const removalNote = removalHint(origin);
  const body = cardBody(plugin);

  return (
    <div className="plugin-detail">
      <button className="plugin-back" onClick={onBack} type="button">
        <ArrowLeft aria-hidden="true" size={15} />
        返回插件列表
      </button>

      <header className="plugin-detail-head">
        <span className="plugin-detail-icon">
          <Icon aria-hidden="true" size={20} />
        </span>
        <div className="plugin-detail-heading">
          <h3>{plugin.label}</h3>
          {plugin.providerLabel ? (
            <p className="plugin-card-owner">{plugin.providerLabel}</p>
          ) : null}
        </div>
        {status ? <StatusBadge label={status.label} tone={status.tone} /> : null}
      </header>

      {plugin.summary ? <p className="plugin-detail-summary">{plugin.summary}</p> : null}
      {plugin.hint ? <p className="plugin-card-hint">{plugin.hint}</p> : null}

      <dl className="plugin-facts">
        {origin ? (
          <div>
            <dt>来源</dt>
            <dd>
              {provenanceLabel(origin)}
              {/* The directory, for the one source a user can act on: it is the
                  thing they cloned, and the thing they need to edit. */}
              {origin.path ? <code>{origin.path}</code> : null}
            </dd>
          </div>
        ) : null}
        {plugin.version ? (
          <div>
            <dt>版本</dt>
            <dd>v{plugin.version}</dd>
          </div>
        ) : null}
        {plugin.tag ? (
          <div>
            <dt>分类</dt>
            <dd>{plugin.tag}</dd>
          </div>
        ) : null}
        {plugin.extensions.length > 0 ? (
          <div>
            <dt>支持的文件类型</dt>
            <dd className="plugin-format-extensions">
              {plugin.extensions.map((extension) => (
                <code key={extension}>{extension}</code>
              ))}
            </dd>
          </div>
        ) : null}
        {plugin.homepage ? (
          <div>
            <dt>主页</dt>
            <dd>
              <a
                className="plugin-tag-link"
                href={plugin.homepage}
                onClick={(event) => {
                  event.preventDefault();
                  void window.docmind.shell.openExternal(plugin.homepage!);
                }}
              >
                了解更多
              </a>
            </dd>
          </div>
        ) : null}
      </dl>

      {origin?.error ? (
        <p className="plugin-failure" role="alert">
          <strong>这个插件没能加载。</strong>
          {origin.error}
        </p>
      ) : null}

      {body !== null ? <div className="plugin-detail-body">{body}</div> : null}

      {/* Rendered whenever there is a plugin to talk about, not only when there
          is a button to draw: the explanation for the *absence* of a button is
          the thing that keeps a missing control from reading as a bug. */}
      {origin !== null ? (
        <div className="plugin-actions">
          {origin.toggleable || origin.removable ? (
            <div className="connection-actions">
              {origin.toggleable ? (
                <button
                  className="button button-secondary"
                  disabled={busy}
                  onClick={() => void switchPlugin(!origin.enabled)}
                  type="button"
                >
                  {toggle.isPending ? (
                    <LoaderCircle aria-hidden="true" className="spin" size={16} />
                  ) : (
                    <Power aria-hidden="true" size={16} />
                  )}
                  {origin.enabled ? "停用插件" : "启用插件"}
                </button>
              ) : null}
              {origin.removable ? (
                <button
                  className="button button-danger-quiet"
                  disabled={busy}
                  onClick={() => setConfirming(true)}
                  type="button"
                >
                  <Trash2 aria-hidden="true" size={16} />
                  移除插件
                </button>
              ) : null}
            </div>
          ) : null}
          {/* Stated, not implied: a change that only takes effect at the next
              start must say so, or the user reloads the page and concludes the
              button does nothing. */}
          {origin.toggleable && !origin.enabled ? (
            <p className="plugin-state-note" role="status">
              已停用，重启 DocMind 后生效。
            </p>
          ) : null}
          {removalNote ? <p className="plugin-card-hint">{removalNote}</p> : null}
          {error ? (
            <p className="error-copy" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}

      {confirming ? (
        <Modal
          backdropClassName="dialog-backdrop-nested"
          className="confirm-dialog"
          initialFocusRef={closeButtonRef}
          labelledBy="plugin-remove-confirm-title"
          onEscape={() => setConfirming(false)}
        >
          <div className="dialog-title-row">
            <h3 id="plugin-remove-confirm-title">确认移除插件</h3>
            <IconButton
              icon={<X aria-hidden="true" size={18} />}
              label="关闭确认窗口"
              onClick={() => setConfirming(false)}
              ref={closeButtonRef}
              size="small"
            />
          </div>
          <p>
            将把「{plugin.label}」移出插件目录，DocMind 重启后不再加载它。
            <strong>插件文件不会被删除</strong>，会移到一个可以找回的位置；
            如果它是你自己 clone 的仓库，之后放回插件目录即可。
          </p>
          {error ? (
            <p className="error-copy" role="alert">
              {error}
            </p>
          ) : null}
          <div className="dialog-actions">
            <button className="button button-secondary" onClick={() => setConfirming(false)}>
              取消
            </button>
            <button className="button button-danger" disabled={busy} onClick={() => void removePlugin()}>
              {remove.isPending ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <Trash2 aria-hidden="true" size={16} />
              )}
              确认移除
            </button>
          </div>
        </Modal>
      ) : null}
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
 * Card bodies by contribution kind.
 *
 * The renderer owns this vocabulary for the same reason it owns the glyph
 * table: a plugin ships as data, so it cannot ship a component. An unknown kind
 * simply gets no body — the row still renders with its declared copy, which
 * beats hiding a plugin that did install.
 *
 * A document format has no body at all: what it adds is its extensions, and
 * those are a fact about the plugin like its version and its homepage, so they
 * are stated once in the facts list rather than twice.
 */
const CARD_BODIES: Record<string, (plugin: PluginManifest) => ReactNode> = {
  remote_source: (plugin) =>
    plugin.hasSecret ? <PluginSecretForm plugin={plugin} /> : <PluginLogin plugin={plugin} />,
};

function cardBody(plugin: PluginManifest): ReactNode {
  const render = CARD_BODIES[plugin.kind];
  return render ? render(plugin) : null;
}

/**
 * Icon keys are declared by plugins as data, so the renderer owns the glyph
 * table. An unknown key falls back to a neutral mark rather than rendering
 * nothing — a third-party plugin may ship an icon this build has never heard
 * of, and it must still look like a row.
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
