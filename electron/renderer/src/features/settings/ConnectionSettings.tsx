import { RemoteProviderCard } from "./RemoteProviderCard";
import { useRemoteProvidersQuery } from "./settings.queries";

/**
 * One card per registered remote provider.
 *
 * The provider list — including which vendors appear at all — comes from the
 * backend registry, so adding a source never touches this file. The summary
 * line is derived from that same list for the same reason: a static sentence
 * here would have to name vendors that a future release may add or drop.
 */
export function ConnectionSettings() {
  const providers = useRemoteProvidersQuery();
  const labels = providers.data?.map((provider) => provider.label) ?? [];

  return (
    <div className="connection-grid">
      <p className="connection-summary">
        {labels.length > 0
          ? `${labels.join("、")}均为可选连接，不影响 DocMind 启动`
          : "远程来源均为可选连接，不影响 DocMind 启动"}
      </p>
      {providers.isPending ? <p className="muted-row">正在加载远程来源…</p> : null}
      {providers.data?.map((provider) => (
        <RemoteProviderCard key={provider.name} provider={provider} />
      ))}
    </div>
  );
}
