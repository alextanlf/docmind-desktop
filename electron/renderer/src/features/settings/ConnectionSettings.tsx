import type { RemoteCredentialChannel } from "../../../../shared/contracts";
import { RemoteProviderCard } from "./RemoteProviderCard";
import {
  useRemoteCredentialsQueries,
  useRemoteProvidersQuery,
} from "./settings.queries";

/**
 * Remote connections, grouped by what each channel is *for* rather than by
 * vendor.
 *
 * Both groups come from the registry — including which vendors appear at all
 * and which of their channels are notification targets — so nothing here names
 * a vendor, and a newly registered source shows up on its own. Grouping by
 * vendor was the previous behaviour and it read as one blob: two unrelated ways
 * of connecting sat under the same heading with no hint of which to pick, and a
 * bot webhook looked like a third way to import documents.
 */
export function ConnectionSettings() {
  const providers = useRemoteProvidersQuery();
  const credentials = useRemoteCredentialsQueries(
    providers.data?.map((provider) => provider.name) ?? [],
  );
  const labels = providers.data?.map((provider) => provider.label) ?? [];

  const channelsOf = (provider: string): RemoteCredentialChannel[] =>
    credentials.data?.[provider] ?? [];

  const has = (provider: string, purpose: "source" | "notify") =>
    channelsOf(provider).some((channel) => (channel.purpose ?? "source") === purpose);

  return (
    <div className="connection-sections">
      <p className="connection-summary">
        {labels.length > 0
          ? `${labels.join("、")}均为可选连接，不影响 DocMind 启动`
          : "远程来源均为可选连接，不影响 DocMind 启动"}
      </p>
      {providers.isPending ? <p className="muted-row">正在加载远程来源…</p> : null}

      <section
        aria-labelledby="connection-sources-title"
        aria-label="知识库来源"
        className="connection-group"
      >
        <h4 id="connection-sources-title">知识库来源</h4>
        <p className="connection-group-hint">
          选一种接入方式即可。同一来源的多种方式不必全填。
        </p>
        <div className="connection-grid">
          {providers.data
            ?.filter((provider) => has(provider.name, "source"))
            .map((provider) => (
              <RemoteProviderCard key={provider.name} provider={provider} purpose="source" />
            ))}
        </div>
        {!providers.isPending &&
        providers.data?.every((provider) => !has(provider.name, "source")) ? (
          <p className="muted-row">尚未注册任何知识库来源。</p>
        ) : null}
      </section>

      <section
        aria-labelledby="connection-notify-title"
        aria-label="通知"
        className="connection-group"
      >
        <h4 id="connection-notify-title">通知</h4>
        <p className="connection-group-hint">
          可选。配置后，导入完成或失败时会向群里发一条消息。
        </p>
        <div className="connection-grid">
          {providers.data
            ?.filter((provider) => has(provider.name, "notify"))
            .map((provider) => (
              <RemoteProviderCard key={provider.name} provider={provider} purpose="notify" />
            ))}
        </div>
        {!providers.isPending &&
        providers.data?.every((provider) => !has(provider.name, "notify")) ? (
          <p className="muted-row">没有可用的通知通道。</p>
        ) : null}
      </section>
    </div>
  );
}

